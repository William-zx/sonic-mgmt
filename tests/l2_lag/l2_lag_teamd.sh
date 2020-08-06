#!/bin/bash

function start_teamd
{
  echo "Startup teamd, PortChannel id is $1"
  pc_id=$1

  for port in $2
  do
  echo "Setting PortChannel member port eth$port in startup teamd process"
  ifconfig eth$port down
  ifconfig eth$port hw ether 00:0c:29:e6:b4:`printf '%02x' $port`
  done

  teamd -f /tmp/PortChannel$pc_id.conf -d
  ifconfig PortChannel$pc_id up
}

function stop_teamd
{
  echo "Stop teamd, PortChannel id is $1"
  pc_id=$1

  teamd -f /tmp/PortChannel$pc_id.conf -k

  for port in $2
  do
  echo "Setting PortChannel member port eth$port in stop teamd process"
  ifconfig eth$port hw ether 00:0c:29:e6:b4:`printf '%02x' $port`
  ifconfig eth$port up
  done
}


case "$1" in
  start)     start_teamd $2 "$3"
               ;;
  stop)      stop_teamd $2 "$3"
               ;;
esac


