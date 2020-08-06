#!/bin/bash

function startup_vlan_test4
{
  echo "Startup portchannel on ptf for test4"

  for((i=1; i<=3; i++))
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0c:29:e6:b4:`printf '%02x' $i`
  teamd -f /tmp/PortChannel$i.conf -d
  ifconfig PortChannel$i up
  ip addr add 172.16.$i.100/24 dev PortChannel$i
  done
}

function delete_vlan_test4
{
  echo "Delete portchannel on ptf for test4"

  for((i=1; i<=3; i++))
  do
  teamd -f /tmp/PortChannel$i.conf -k
  ifconfig eth$i hw ether 00:0c:29:e6:b4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_vlan_test5
{
  echo "Startup portchannel on ptf for test5"

  for((i=1; i<=3; i++))
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0c:29:e6:b4:`printf '%02x' $i`
  teamd -f /tmp/PortChannel$i.conf -d
  ifconfig PortChannel$i up
  ip addr add 172.16.$i.100/24 dev PortChannel$i
  done
}

function delete_vlan_test5
{
  echo "Delete portchannel on ptf for test5"

  for((i=1; i<=3; i++))
  do
  teamd -f /tmp/PortChannel$i.conf -k
  ifconfig eth$i hw ether 00:0c:29:e6:b4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_vlan_test6
{
  echo "Config ip for eth1 on ptf"
  ip addr add 100.100.100.2/16 dev eth1
}

function delete_vlan_test6
{
  echo "Delete ip for eth1 on ptf"
  ip addr del 100.100.100.2/16 dev eth1  
}

function startup_vlan_test7
{
  echo "Config ipv6 for eth1 on ptf"
  ip addr add 2000::2/64 dev eth1
}

function delete_vlan_test7
{
  echo "Delete ipv6 for eth1 on ptf"
  ip addr del 2000::2/64 dev eth1
}

function startup_vlan_test8
{
  ifconfig eth1 hw ether 00:11:11:11:11:11
  ifconfig eth2 hw ether 00:22:22:22:22:22
  ip addr add 100.1.1.2/16 dev eth1
  ip addr add 100.2.1.2/16 dev eth2
}

function delete_vlan_test8
{
  ifconfig eth1 hw ether 00:0c:29:e6:b4:01
  ifconfig eth2 hw ether 00:0c:29:e6:b4:02
  ip addr del 100.1.1.2/16 dev eth1
  ip addr del 100.2.1.2/16 dev eth2
}

function add_route_test8
{
  ip route add 100.3.0.0/16 via 100.1.1.1
}

function delete_route_test8
{
  ip route del 100.3.0.0/16 via 100.1.1.1
}

case "$1" in
  startup_vlan_test4)   startup_vlan_test4
               ;;
  delete_vlan_test4)    delete_vlan_test4
               ;;
  startup_vlan_test5)   startup_vlan_test5
               ;;
  delete_vlan_test5)    delete_vlan_test5
               ;;
  startup_vlan_test6)   startup_vlan_test6
               ;;
  delete_vlan_test6)    delete_vlan_test6
               ;;
  startup_vlan_test7)   startup_vlan_test7
               ;;
  delete_vlan_test7)    delete_vlan_test7
               ;;
  startup_vlan_test8)   startup_vlan_test8
               ;;
  delete_vlan_test8)    delete_vlan_test8
               ;;
  add_route_test8)      add_route_test8
               ;;
  delete_route_test8)   delete_route_test8
               ;;
esac


