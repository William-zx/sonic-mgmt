#!/bin/bash

function startup_portchannel_test1
{
  echo "Startup portchannel on ptf for test1"
  portchannel_id=1

  for((i=1; i<=8; i++));
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  done

  teamd -f /tmp/PortChannel$portchannel_id.conf -d
  ifconfig PortChannel$portchannel_id up
  ip addr add 172.16.$portchannel_id.100/24 dev PortChannel$portchannel_id
}

function delete_portchannel_test1
{
  echo "Delete portchannel on ptf for test1"
  portchannel_id=1

  teamd -f /tmp/PortChannel$portchannel_id.conf -k

  for((i=1;i<=8;i++));
  do
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_portchannel_test2
{
  echo "Startup portchannel on ptf for test2"

  for((i=1;i<=16;i++));
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  teamd -f /tmp/PortChannel$i.conf -d
  ifconfig PortChannel$i up
  ip addr add 100.1.$i.2/24 dev PortChannel$i
  done
}

function delete_portchannel_test2
{
  echo "Delete portchannel on ptf for test2"

  for((i=1;i<=16;i++));
  do
  teamd -f /tmp/PortChannel$i.conf -k
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_portchannel_test10
{
  echo "Startup portchannel on ptf"
  portchannel_id=1

  for((i=1; i<=4; i++));
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  done

  teamd -f /tmp/PortChannel$portchannel_id.conf -d
  ifconfig PortChannel$portchannel_id up
  ip addr add 2000::2/64 dev PortChannel$portchannel_id
}

function delete_portchannel_test10
{
  echo "Delete portchannel on ptf"
  portchannel_id=1

  teamd -f /tmp/PortChannel$portchannel_id.conf -k

  for((i=1;i<=4;i++));
  do
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_portchannel_test11
{
  echo "Startup portchannel on ptf"
  portchannel_id=1

  for((i=1; i<=4; i++));
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  done

  teamd -f /tmp/PortChannel$portchannel_id.conf -d
  ifconfig PortChannel$portchannel_id up
  ip addr add 100.1.1.2/16 dev PortChannel$portchannel_id
}

function delete_portchannel_test11
{
  echo "Delete portchannel on ptf"
  portchannel_id=1

  teamd -f /tmp/PortChannel$portchannel_id.conf -k

  for((i=1;i<=4;i++));
  do
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_portchannel_test12
{
  echo "Startup portchannel on ptf"
  portchannel_id=1

  for((i=1; i<=4; i++));
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  done

  teamd -f /tmp/PortChannel$portchannel_id.conf -d
  ifconfig PortChannel$portchannel_id up
  ip addr add 2000::2/64 dev PortChannel$portchannel_id
}

function delete_portchannel_test12
{
  echo "Delete portchannel on ptf"
  portchannel_id=1

  teamd -f /tmp/PortChannel$portchannel_id.conf -k

  for((i=1;i<=4;i++));
  do
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_portchannel_test13
{
  echo "Startup portchannel on ptf"
  portchannel_id=1

  for((i=1; i<=4; i++));
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  done

  teamd -f /tmp/PortChannel$portchannel_id.conf -d
  ifconfig PortChannel$portchannel_id up
  ip addr add 100.1.1.2/16 dev PortChannel$portchannel_id
}

function delete_portchannel_test13
{
  echo "Delete portchannel on ptf"
  portchannel_id=1

  teamd -f /tmp/PortChannel$portchannel_id.conf -k

  for((i=1;i<=4;i++));
  do
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_portchannel_test14
{
  echo "Startup portchannel on ptf"

  ifconfig eth1 down
  ifconfig eth1 hw ether 00:0C:29:E6:B4:01
  teamd -f /tmp/PortChannel1.conf -d
  ifconfig PortChannel1 up
  ip addr add 100.1.1.2/16 dev PortChannel1

  ifconfig eth2 down
  ifconfig eth2 hw ether 00:0C:29:E6:B4:02
  teamd -f /tmp/PortChannel2.conf -d
  ifconfig PortChannel2 up
  ip addr add 100.2.1.2/16 dev PortChannel2
}

function delete_portchannel_test14
{
  echo "Delete portchannel on ptf"

  teamd -f /tmp/PortChannel1.conf -k
  ifconfig eth1 hw ether 00:0C:29:E6:B4:01
  ifconfig eth1 up

  teamd -f /tmp/PortChannel2.conf -k
  ifconfig eth2 hw ether 00:0C:29:E6:B4:01
  ifconfig eth2 up
}

function add_route_test14
{
  ip route add 100.3.0.0/16 via 100.1.1.1
}

function delete_route_test14
{
  ip route del 100.3.0.0/16 via 100.1.1.1
}

function startup_portchannel_general
{
  echo "Startup portchannel on ptf"
  portchannel_id=1

  for((i=1; i<=4; i++));
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  done

  teamd -f /tmp/PortChannel$portchannel_id.conf -d
  ifconfig PortChannel$portchannel_id up
  ip addr add 100.1.1.2/24 dev PortChannel$portchannel_id
}

function delete_portchannel_general
{
  echo "Delete portchannel on ptf"
  portchannel_id=1

  teamd -f /tmp/PortChannel$portchannel_id.conf -k

  for((i=1;i<=4;i++));
  do
  ifconfig eth$i hw ether 00:0C:29:E6:B4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

case "$1" in
  startup_portchannel_test1)       startup_portchannel_test1
               ;;
  delete_portchannel_test1)        delete_portchannel_test1
               ;;
  startup_portchannel_test2)       startup_portchannel_test2
               ;;
  delete_portchannel_test2)        delete_portchannel_test2
               ;;
  startup_portchannel_test10)      startup_portchannel_test10
               ;;
  delete_portchannel_test10)       delete_portchannel_test10
               ;;
  startup_portchannel_test11)      startup_portchannel_test11
               ;;
  delete_portchannel_test11)       delete_portchannel_test11
               ;;
  startup_portchannel_test12)      startup_portchannel_test12
               ;;
  delete_portchannel_test12)       delete_portchannel_test12
               ;;
  startup_portchannel_test13)      startup_portchannel_test13
               ;;
  delete_portchannel_test13)       delete_portchannel_test13
               ;;
  startup_portchannel_test14)      startup_portchannel_test14
               ;;
  delete_portchannel_test14)       delete_portchannel_test14
               ;;
  add_route_test14)                add_route_test14
               ;;
  delete_route_test14)             delete_route_test14
               ;;    
  startup_portchannel_general)     startup_portchannel_general
               ;;
  delete_portchannel_general)      delete_portchannel_general
               ;;
esac
