### supported topo
  - ptf32
  - t0

### Virtual topo
<vs_virtual_topo>[vs_virtual_topo]
  
  
### Pre request
Hardware:
 - same as vm_host

Software:
 - vm image
 - sonic-mgmt docker
 - ptf docker
 - vs kvm image
 - install package 'expect' on vs_host
### IP distribution
Use 10.168.100.0/24 for management of sonic ansible test
VM host:    10.168.100.245
Gateway:    10.168.100.1
VSs:        10.168.100.151~10.168.100.199
PTFs:       10.168.100.100~10.168.100.150
VMs:        10.168.100.2~10.168.100.99
sonic-mgmt: 10.168.100.200~10.168.100.229
 
### deploy sequence
- setup files on vs_host
  copy vm hdd/iso to vs_host
  copy docker-ptf to vs_host
  copy vs kvm image to vs_host

- checkout your sonic-mgmt repository
  
- modify testbed-vs.yml to fixing your hw ,sw, network configuraion.

- start sonic-mgmt docker container.
    docker run --rm \
          --privileged \
          --name ${SONIC_MGMT_DOCKER_NAME} \
          --net=none \
          -u 0:0 \
          -dt \
          docker-sonic-mgmt bash

- generate sonic-mgmt configuration files.
  
  Enter sonic-mgmt docker, cd /path/to/ansible-root
  `python TestbedProcessing.py -i testbed-vs.yml`
  `cd files && python creategraph.py -o lab_connection_graph.xml`

- create mgmt_bridge and vextif on vs_host. 
  
  We could use testbed-cli-vs.sh to generate the network init shell script.  Enter sonic-mgmt docker, cd ansible root path:
  `./testbed-cli-vs.sh gen-vs-host-sh ${vs-topo-name} ~/.password`
  copy the output shell script to vs_host and execute it.
  `docker cp ${SONIC_MGMT_DOCKER_NAME}:/tmp/init_vs_host_network.sh ./`

- connect sonic-mgmt to vs_host mgmt_bridge.
  
  For example, if sonic-mgmt docker running on vs_host, we can use veth peer to connect sonic-mgmt to mgmt_bridge. E.g. :
  `ip link add <sonic-mgmt-interface> type veth peer name <sonic-mgmt-interface-peer>`
  `ip link set <sonic-mgmt-interface-peer> netns $(docker inspect -f {{.State.Pid}} ${SONIC_MGMT_DOCKER_NAME})`
  `brctl addif ${mgmt_bridge} ${sonic-mgmt-interface}`
  `ip link set <sonic-mgmt-interface> up`
  `docker exec -it ${SONIC_MGMT_DOCKER_NAME} ip link set <sonic-mgmt-interface-peer> up`
  `docker exec -it ${SONIC_MGMT_DOCKER_NAME} ip addr add <sonic-mgmt-ip>/<mask> dev <sonic-mgmt-interface-peer>`


- Deploy
./testbed-cli-vs.sh start-vms VS-SERV-01
./testbed-cli-vs.sh start-vs vs-t0-1
./testbed-cli-vs.sh add-topo-vs vs-t0-1

- Destroy
./testbed-cli-vs.sh remove-topo-vs vs-t0-1
./testbed-cli-vs.sh stop-vs vs-t0-1
./testbed-cli-vs.sh stop-vms VS-SERV-01

### Supported script (Refer to SONiC201811_Autotest_Summary.xlsx)
