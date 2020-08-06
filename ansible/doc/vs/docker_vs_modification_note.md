### apt-get install sudo

### add machine.conf
onie_version=2016.11
onie_vendor_id=2468
onie_platform=x86_64-dell_s6000_s1220-r0
onie_machine=Force10-S6000
onie_machine_rev=0
onie_arch=x86_64
onie_config_version=1
onie_build_date="2017-07-07T14:53+0800"
onie_partition_type=gpt
onie_kernel_version=4.1.34
onie_firmware=bios
onie_switch_asic=nephos
onie_skip_ethmgmt_macs=no


### add sonic_version.yml (in latest（2018/12/12）vs docker, no longer do this)
build_version: 'master.0-dirty-20181012.033514'
debian_version: '8.11'
kernel_version: '3.16.0-5-amd64'
asic_type: nephos
commit_id: 'b3ce315'
build_date: Fri Oct 12 03:39:55 UTC 2018
build_number: 0
built_by: chenhu@localhost

### touch updategraph.conf

### Enable sshd
- add sshd.conf
    ```
    mkdir /var/run/sshd
    vi /etc/ssh/sshd_config 
      PermitRootLogin yes
      UseDNS no
    # Setup password of root
    passwd root
    ```
- Create supervisor configuration of sshd
in _/etc/supervisor/conf.d/supervisor.conf_
    ```
    [program:sshd]
    command=/usr/sbin/sshd -D
    process_name=sshd
    stdout_logfile=/tmp/sshd.out.log
    stderr_logfile=/tmp/sshd.err.log
    redirect_stderr=false
    autostart=false
    autorestart=true
    startsecs=1
    numprocs=1
    ```
- Start sshd in /usr/bin/start.sh

### Enable bgp
- add /etc/sonic/deployment_id_asn_map.yml 
    deployment_id_asn_map:
      "1" : 65432
- Create start_quagga.sh for generating zebra.conf and bgp.conf based on config_db
  start_quagga.sh
      #!/bin/bash
      mkdir -p /etc/quagga
      sonic-cfggen -d -y /etc/sonic/deployment_id_asn_map.yml -t /usr/share/sonic/templates/bgpd.conf.j2 > /etc/quagga/bgpd.conf
      sonic-cfggen -d -t /usr/share/sonic/templates/zebra.conf.j2 > /etc/quagga/zebra.conf

      supervisorctl start zebra
      supervisorctl start bgpd
      supervisorctl start fpmsyncd
- Create supervisor configuration of bgp
    [program:start_quagga]
    command=/usr/bin/start_quagga.sh
    process_name=start_quagga
    stdout_logfile=syslog
    stderr_logfile=syslog
    autostart=false
    autorestart=false  
- modify start.sh to fixed above modification

### enable hostname
- Create hostname-config.sh
    #!/bin/bash -e

    CURRENT_HOSTNAME=`hostname`
    HOSTNAME=`sonic-cfggen -d -v DEVICE_METADATA[\'localhost\'][\'hostname\']|tr _ '-'`

    echo $HOSTNAME > /etc/hostname
    hostname -F /etc/hostname

    sed "/\s$CURRENT_HOSTNAME$/d" /etc/hosts > /etc/hosts.new
    cat /etc/hosts.new > /etc/hosts
    echo "127.0.0.1 $HOSTNAME" >> /etc/hosts
- Create supervisor configuration of hostname-config
[program:hostname-config]
command=/usr/bin/hostname-config.sh
process_name=hostname-config
stdout_logfile=syslog
stderr_logfile=syslog
autostart=false
autorestart=false
- start hostname-config in start.sh
supervisorctl start hostname-config