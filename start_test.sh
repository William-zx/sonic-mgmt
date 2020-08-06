#!/bin/bash -xe

printenv
SCRIPT_DIR=$(cd $(dirname $0);pwd)
HTTP_SERV_PORT=5678

# BUILD_NUMBER setting by jenkins.
# If not invoke from jenkins, set it to be 1
if [ -z "$BUILD_NUMBER" ]; then
    BUILD_NUMBER=1
fi
SONIC_MGMT_DOCKER_NAME="smgmt-jk-${BUILD_NUMBER}"

if [ -z "${registry_host}" ]; then
    SONIC_MGMT_IMAGE_NAME="docker-sonic-mgmt"
else
    SONIC_MGMT_IMAGE_NAME="${registry_host}/docker-sonic-mgmt"
fi

MGMT_BRIDGE='br1'
DOCKER_NET_TYPE='host'   

function clean_up_on_exit() 
{
    docker stop --time=5 ${SONIC_MGMT_DOCKER_NAME} || true

    if [ -n ${sonic_image} ]; then
        python ${SCRIPT_DIR}/utils/simple_http_server -s || true
    fi
}

function usage
{
  set +x
  echo "Start running ansible testcases in a chunk."
  echo "Usage:"
  echo "    $0 [options]"
  echo
  echo "Options:"
  echo "    -t <tbcfgfile>      : testbed configuration file name for generate testbed info by ansible/TestbedProcessing.py."
  echo "                           It should exists in ./testbed/ "
  echo "                           Without this option, will try to detect configuration file by tbutil.py"
  echo "    -i <topo2casesfile> : testcases file name."
  echo "                           Wihtout this option , will executes all applicable testcases on specified platform "
  echo "    -p <platform>       : vskvm or dut.(case sensitive)"
  echo "                           Without this option, will try to detect platform by tbutil.py"
  echo "    -s <sonic_image>    : sonic image to be upgrade"
  echo "    -d                  : Dry run. With this option, all actions will only be logged instead of actually executed. "
  echo "    -r <registry_host>  : Registry host."
  echo ""
  echo "To start ansible test on VS: "
  echo "    $0 -t testbed_143.yml -p vskvm"
  echo "To start ansible test on dut with specificed cases in file: "
  echo "    $0 -t testbed_xxx.yml -i testcases.yml -p dut"
  exit 1
}

function main() 
{
    case "${platform}" in
        'dut')
            LOG_DIR='dutlog'
            ;;
        'vskvm')
            LOG_DIR='vslog'
            ;;
        *)
            echo "Unkonwn platform: $platform !!"
            exit 1
            ;;
    esac

    trap clean_up_on_exit 0 1 15

    # pull sonic-mgmt image if not found
    if [ -z "$(docker images -q ${SONIC_MGMT_IMAGE_NAME})" ]; then
        docker pull ${SONIC_MGMT_IMAGE_NAME}
    fi

    # start sonic-mgmt docker container
    docker run --rm \
            --privileged \
            --name ${SONIC_MGMT_DOCKER_NAME} \
            --net=${DOCKER_NET_TYPE} \
            -u 0:0 \
            -v ${SCRIPT_DIR}:/var/root/sonic-mgmt-jenkins:rw  \
            -v ${SCRIPT_DIR}/${LOG_DIR}:/tmp:rw \
            -w /var/root/sonic-mgmt-jenkins \
            -td \
            ${SONIC_MGMT_IMAGE_NAME} bash

    # update testbed with testbed configuration file
    docker exec ${SONIC_MGMT_DOCKER_NAME} bash -c "
        touch ~/.password
        pushd ansible
        python TestbedProcessing.py -i ../testbed/${tbcfgfile}
        pushd files
        python creategraph.py -o lab_connection_graph.xml
        "

    # FIXME should use a common http server instead of setting up a server on sonic-mgmt
    # set up a simple http server for dut upgrading only for dut platform
    if [ -n "${sonic_image}"  ] && [ -f "${sonic_image}" ] ; then
        http_server_ip=$(ip -4 --br addr show ${MGMT_BRIDGE}  | awk '{print $3}' | cut -d/ -f1)
        python ${SCRIPT_DIR}/utils/simple_http_server -d ${SCRIPT_DIR} -i ${http_server_ip} -p ${HTTP_SERV_PORT} &
        sonic_image_url="http://${http_server_ip}:${HTTP_SERV_PORT}/${sonic_image}"
    fi

    # Start vms
    vm_list=$(docker exec -w /var/root/sonic-mgmt-jenkins/ansible ${SONIC_MGMT_DOCKER_NAME} ansible -i veos eos --list-hosts 2>/dev/null| grep -o VM.*)
    vm_host=$(docker exec -w /var/root/sonic-mgmt-jenkins/ansible ${SONIC_MGMT_DOCKER_NAME} ansible -i veos vm_host --list-hosts 2>/dev/null| sed "1d;s/ *//g")
    running_vm_list=$(virsh list --name --state-running)
    respin_vms=()

    # if vm is running bug can not ping OK
    # add it to respin_vms for re-create
    for vm in ${vm_list};
    do
        if [[ $running_vm_list =~ $vm ]]; then
            docker exec -w /var/root/sonic-mgmt-jenkins/ansible ${SONIC_MGMT_DOCKER_NAME} ansible -T1 -m ping -i veos $vm  || respin_vms+=($vm)
        fi
    done

    docker exec -it -w /var/root/sonic-mgmt-jenkins/ansible ${SONIC_MGMT_DOCKER_NAME} \
        ./testbed-cli.sh start-vms ${vm_host} ~/.password -m veos.vtb -e batch_size=4 -e respin_vms=[$(IFS=,;echo "${respin_vms[*]}")]

    # Start test
    cmd="python autotest.py -b ${BUILD_NUMBER} -l info -p ${platform} "
    if [ -n "${topo2casesfile}" ]; then
        cmd+=" -i $(realpath --relative-to=ansible ${topo2casesfile})"
    fi
    if [ -n "${tbcfgfile}" ]; then
        cmd+=" -c ../testbed/${tbcfgfile}"
    fi
    if [ -n "${sonic_image}" ] && [ -f "${sonic_image}" ] ; then
        cmd+=" -s ${sonic_image_url}"
    fi
    if [ ${dry} ]; then
        cmd+=" --dry"
    fi
    
    docker exec -it ${SONIC_MGMT_DOCKER_NAME} bash -c "
        cd ansible
        ${cmd}
    "
}

while getopts "r:t:i:p:s:d" OPTION; do
    case $OPTION in
        r)
            registry_host=$OPTARG
            ;;
        t)
            tbcfgfile=$OPTARG
            ;;
        i)
            topo2casesfile=$OPTARG
            ;;
        p)
            platform=$OPTARG
            ;;
        s)
            sonic_image=$OPTARG
            ;;
        d)
            dry=true
            ;;
        *)
            usage
            ;;
    esac
done

if [[ -z ${tbcfgfile} || -z ${platform} ]]; then
    tbcfgfile=`python ./testbed/tbutil.py -t`
    platform=`python ./testbed/tbutil.py -p`
fi

main