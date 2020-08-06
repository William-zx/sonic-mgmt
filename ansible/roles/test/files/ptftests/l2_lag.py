import ast
import logging
import random

from ipaddress import ip_address, ip_network

import ptf
import ptf.packet as scapy
import ptf.dataplane as dataplane

from ptf.base_tests import BaseTest
from ptf.testutils import *
from ptf.mask import Mask


class LagTest(BaseTest):
    #---------------------------------------------------------------------
    # Class variables
    #---------------------------------------------------------------------
    DEFAULT_BALANCING_RANGE = 0.25
    BALANCING_TEST_TIMES = 1000

    def __init__(self):
        '''
        @summary: constructor
        '''
        BaseTest.__init__(self)
        self.test_params = test_params_get()

    #---------------------------------------------------------------------

    def setUp(self):
        '''
        @summary: Setup for the test
        Two test parameters are used:
         - dst_ip: the IP address to creat the eth_ip of the packets
         - dst_mac: the MAC address to create the eth_dst of the packet
         - src_port: src_port will send packets
         - dst_ports: dst_port list will receive packets
         - balance: if the value is 'mac', will send packets with src_mac changes,
           if is 'ip', will send packets with src_ip changes
         - pkt_action: expect to 'receive' or 'not receive' the packets.'fwd' is expect to 'receive'
        '''
        self.dataplane            = ptf.dataplane_instance
        self.src_mac              = self.test_params['src_mac']
        self.dst_mac              = self.test_params['dst_mac']
        self.src_port             = self.test_params['src_port']
        self.dst_ports            = self.test_params['dst_ports']
        self.vlan                 = int(self.test_params.get('vlan', 0))
        self.dst_ip               = self.test_params.get('dst_ip', None)
        self.pkt_action           = self.test_params.get('pkt_action', 'fwd')
        self.pkt_type             = 'L3' if self.dst_ip else 'L2'
        self.balance              = self.test_params.get('balance', None)
        self.balancing_range      = self.test_params.get('balancing_range', self.DEFAULT_BALANCING_RANGE)

    #---------------------------------------------------------------------

    def check_traffic(self):
            
            (pkt, masked_exp_pkt) = self.generate_packet()

            send_packet(self, self.src_port, pkt)
            logging.info(
                        "\n Sending packet with ip {} from port {} to {}".format(self.dst_ip, self.src_port, self.dst_ports) + \
                        "\n packet_action : {}".format(self.pkt_action) + \
                        "\n pkt_type: {}".format(self.pkt_type)
                        )

            if self.pkt_action == 'fwd':
                (matched_index, received) = verify_packet_any_port(self, masked_exp_pkt, self.dst_ports)
                assert received
                # check lag load balance
                if self.balance:
                    logging.info("Check PortChannel member balancing...")
                    hit_count_map = {}
                    for i in range(0, self.BALANCING_TEST_TIMES):
                        (pkt, masked_exp_pkt) = self.generate_packet()
                        send_packet(self, self.src_port, pkt)
                        (matched_index, received) = verify_packet_any_port(self, masked_exp_pkt, self.dst_ports)
                        matched_port = self.dst_ports[matched_index]
                        hit_count_map[matched_port] = hit_count_map.get(matched_port, 0) + 1
                    self.check_balancing(hit_count_map)
            else:
                verify_no_packet_any(self, masked_exp_pkt, self.dst_ports)

    #---------------------------------------------------------------------

    def generate_packet(self):
        if self.pkt_type == 'L2':
            src_mac = "00:10:94:00:{:x}:{:x}".format(random.randint(0, 255), random.randint(0, 255)) if self.balance == 'mac' else self.src_mac
            src_ip  = "10.0.{}.{}".format(random.randint(0, 255), random.randint(0, 255)) if self.balance == 'ip' else '10.0.0.1'
            vlan_enable = True if self.vlan !=0 else False
            pkt = simple_ip_packet( eth_dst=self.dst_mac,
                                    eth_src=src_mac,
                                    dl_vlan_enable=vlan_enable,
                                    vlan_vid=self.vlan,
                                    ip_src=src_ip,
                                    ip_dst='192.168.0.2'
                                )
            masked_exp_pkt = Mask(pkt)

        else:
            sport   = random.randint(0, 65535)
            dport   = random.randint(0, 65535)
            src_mac = self.dataplane.get_mac(0, 0)

            version = ip_network(unicode(self.dst_ip)).version
            ip_src  = "10.0.0.1" if version == 4 else "2000::1"
            ip_dst  = self.dst_ip

            pkt_args     = {
                'eth_src':   src_mac,
                'eth_dst':   self.dst_mac,
                'tcp_sport': sport,
                'tcp_dport': dport
            }

            exp_pkt_args = {
                'eth_src':   self.dst_mac,
                'tcp_sport': sport,
                'tcp_dport': dport
            }

            if version == 4:
                pkt_args.update({'ip_src': ip_src, 'ip_dst': ip_dst, 'ip_ttl': 64})
                exp_pkt_args.update({'ip_src': ip_src, 'ip_dst': ip_dst, 'ip_ttl': 63})
                pkt     = simple_tcp_packet(**pkt_args)
                exp_pkt = simple_tcp_packet(**exp_pkt_args)
            else:
                pkt_args.update({'ipv6_src': ip_src, 'ipv6_dst': ip_dst, 'ipv6_hlim': 64})
                exp_pkt_args.update({'ipv6_src': ip_src, 'ipv6_dst': ip_dst, 'ipv6_hlim': 63})
                pkt     = simple_tcpv6_packet(**pkt_args)
                exp_pkt = simple_tcpv6_packet(**exp_pkt_args)

            masked_exp_pkt = Mask(exp_pkt)
            masked_exp_pkt.set_do_not_care_scapy(scapy.Ether, "dst")

        return (pkt, masked_exp_pkt)

    #---------------------------------------------------------------------

    def check_within_expected_range(self, actual, expected):
        '''
        @summary: Check if the actual number is within the accepted range of the expected number
        @param actual : acutal number of recieved packets
        @param expected : expected number of recieved packets
        @return (percentage, bool)
        '''
        percentage = (actual - expected) / float(expected)
        return (percentage, abs(percentage) <= self.balancing_range)

    #---------------------------------------------------------------------

    def check_balancing(self, port_hit_cnt):
            '''
            @summary: Check if the traffic is balanced across the LAG members
            @param port_hit_cnt : a dict that records the number of packets each port received
            @return bool
            '''

            logging.info("%-10s \t %-10s \t %10s \t %10s \t %10s" % ("type", "port(s)", "exp_cnt", "act_cnt", "diff(%)"))
            result = True

            total_hit_cnt = sum(port_hit_cnt.values())

            for port in self.dst_ports:
                (p, r) = self.check_within_expected_range(port_hit_cnt.get(port, 0), float(total_hit_cnt)/len(self.dst_ports))
                logging.info("%-10s \t %-10s \t %10d \t %10d \t %10s"
                    % ("LAG", str(port), total_hit_cnt/len(self.dst_ports), port_hit_cnt.get(port, 0), str(round(p, 4)*100) + '%'))
                result &= r

            assert result

    # ---------------------------------------------------------------------

    def runTest(self):
        self.check_traffic()