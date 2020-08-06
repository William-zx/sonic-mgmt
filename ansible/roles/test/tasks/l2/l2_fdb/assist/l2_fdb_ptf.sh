#!/bin/bash

function startup_fdb_test10
{
  echo "Startup portchannel on ptf for fdb_test10"
  pc_id=1

  for((i=1; i<=4; i++))
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0c:29:e6:b4:`printf '%02x' $i`
  done

  teamd -f /tmp/PortChannel$pc_id.conf -d
  ifconfig PortChannel$pc_id up
  ip addr add 172.16.$pc_id.100/24 dev PortChannel$pc_id
}

function delete_fdb_test10
{
  echo "Delete portchannel on ptf for fdb_test10"
  pc_id=1

  teamd -f /tmp/PortChannel$pc_id.conf -k

  for((i=1; i<=4; i++))
  do
  ifconfig eth$i hw ether 00:0c:29:e6:b4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}

function startup_fdb_test11
{
  echo "Startup portchannel on ptf for fdb_test11"
  pc_id=1

  for((i=1; i<=2; i++))
  do
  ifconfig eth$i down
  ifconfig eth$i hw ether 00:0c:29:e6:b4:`printf '%02x' $i`
  done
  teamd -f /tmp/PortChannel$pc_id.conf -d
  ifconfig PortChannel$pc_id up
  ip addr add 172.16.$pc_id.100/24 dev PortChannel$pc_id
}

function delete_fdb_test11
{
  echo "Delete portchannel on ptf for fdb_test11"
  pc_id=1

  teamd -f /tmp/PortChannel$pc_id.conf -k

  for((i=1; i<=2; i++))
  do
  ifconfig eth$i hw ether 00:0c:29:e6:b4:`printf '%02x' $i`
  ifconfig eth$i up
  done
}


case "$1" in
  startup_fdb_test10)   startup_fdb_test10
               ;;
  delete_fdb_test10)    delete_fdb_test10
               ;;
  startup_fdb_test11)   startup_fdb_test11
               ;;
  delete_fdb_test11)    delete_fdb_test11
               ;;
esac


