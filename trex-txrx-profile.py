from __future__ import print_function

import sys, getopt
sys.path.append('/opt/trex/current/automation/trex_control_plane/stl/examples')
sys.path.append('/opt/trex/current/automation/trex_control_plane/stl')
import argparse
import stl_path
import string
import datetime
import math
import threading
import thread
from decimal import *
from trex_stl_lib.api import *
from trex_tg_lib import *

class t_global(object):
     args=None

def myprint(*args, **kwargs):
     stderr_only = False
     if 'stderr_only' in kwargs:
          stderr_only = kwargs['stderr_only']
          del kwargs['stderr_only']
     if not stderr_only:
          print(*args, **kwargs)
     if stderr_only or t_global.args.mirrored_log:
          print(*args, file = sys.stderr, **kwargs)
     return

def process_options ():
    parser = argparse.ArgumentParser(usage="generate network traffic and report packet loss")

    parser.add_argument('--debug',
                        dest='debug',
                        help='Should debugging be enabled',
                        action = 'store_true'
                        )
    parser.add_argument('--mirrored-log',
                        dest='mirrored_log',
                        help='Should the logging sent to STDOUT be mirrored on STDERR',
                        action = 'store_true',
                        )
    parser.add_argument('--device-pairs',
                        dest='device_pairs',
                        help='List of device pairs in the for A:B[,C:D][,E:F][,...]',
                        default="0:1",
                        )
    parser.add_argument('--active-device-pairs',
                        dest='active_device_pairs',
                        help='List of active device pairs in the for A:B[,C:D][,E:F][,...]',
                        default="--",
                        )
    parser.add_argument('--traffic-direction', 
                        dest='traffic_direction',
                        help='Direction for traffic to flow between active device pairs',
                        default = 'bidirec',
                        choices = [ 'bidirec', 'unidirec', 'revunidirec' ]
                        )
    parser.add_argument('--runtime', 
                        dest='runtime',
                        help='trial period in seconds',
                        default=30,
                        type = int,
                        )
    parser.add_argument('--runtime-tolerance',
                        dest='runtime_tolerance',
                        help='The percentage of time that the test is allowed in excess of the requested runtime before it is stopped',
                        default=5,
                        type = float,
                        )
    parser.add_argument('--measure-latency',
                        dest='measure_latency',
                        help='Collect latency statistics or not',
                        action = 'store_true'
                        )
    parser.add_argument('--latency-rate',
                        dest='latency_rate',
                        help='Rate to send latency packets per second',
                        default = 1000,
                        type = int
                        )
    parser.add_argument('--skip-hw-flow-stats',
                        dest='skip_hw_flow_stats',
                        help='Should hardware flow stat support be used',
                        action = 'store_true',
                        )
    parser.add_argument('--max-loss-pct',
                        dest='max_loss_pct',
                        help='Maximum percentage of packet loss',
                        default=0.002,
                        type = float
                        )
    parser.add_argument('--disable-flow-cache',
                        dest='enable_flow_cache',
                        help='Force disablement of the flow cache',
                        action = 'store_false',
                        )
    parser.add_argument('--send-teaching-warmup',
                        dest='send_teaching_warmup',
                        help='Send teaching packets from the receiving port during a warmup phase',
                        action = 'store_true',
                        )
    parser.add_argument('--send-teaching-measurement',
                        dest='send_teaching_measurement',
                        help='Send teaching packetsfrom the receiving port during the measurement phase',
                        action = 'store_true',
                        )
    parser.add_argument('--teaching-measurement-interval',
                        dest='teaching_measurement_interval',
                        help='Interval to send teaching packets on from the receiving port during the measurement phase in seconds',
                        default = 10.0,
                        type = float
                        )
    parser.add_argument('--teaching-warmup-packet-rate',
                        dest='teaching_warmup_packet_rate',
                        help='Rate to send teaching packets at from the receiving port in packets per second (pps) during the warmup',
                        default = 1000,
                        type = int
                        )
    parser.add_argument('--teaching-measurement-packet-rate',
                        dest='teaching_measurement_packet_rate',
                        help='Rate to send teaching packets at from the receiving port in packets per second (pps) during the measurement phase',
                        default = 1000,
                        type = int
                        )
    parser.add_argument('--teaching-warmup-packet-type',
                        dest='teaching_warmup_packet_type',
                        help='Type of packet to send for the teaching warmup from the receiving port',
                        default = 'garp',
                        choices = ['garp', 'icmp', 'bulk']
                        )
    parser.add_argument('--teaching-measurement-packet-type',
                        dest='teaching_measurement_packet_type',
                        help='Type of packet to send for the teaching measurement from the receiving port',
                        default = 'garp',
                        choices = ['garp', 'icmp', 'bulk']
                        )
    parser.add_argument('--traffic-profile',
                        dest='traffic_profile',
                        help='Name of the file containing traffic profiles to load',
                        default = '',
                        type = str
                        )

    t_global.args = parser.parse_args()

    if t_global.args.active_device_pairs == '--':
         t_global.args.active_device_pairs = t_global.args.device_pairs

    myprint(t_global.args)

def load_traffic_profile (profile):
    for stream in profile['streams']:
        for key in stream:
            if isinstance(stream[key], basestring):
                fields = stream[key].split(':')
                if len(fields) == 2:
                    if fields[0] == 'function':
                        stream[key] = eval(fields[1])

    return profile

def create_stream (stream, device_pair, direction, other_direction):
    stream_types = [ 'measurement' ]
    if 'stream_types' in stream:
        stream_types = stream['stream_types']

    frame_type = 'bulk'
    if 'frame_type' in stream:
        frame_type = stream['frame_type']

    latency_only = False
    if 'latency_only' in stream:
        latency_only = stream['latency_only']

    latency = True
    if not t_global.args.measure_latency:
        latency = False
    elif 'latency' in stream:
        latency = stream['latency']

    protocol = 'UDP'
    if 'protocol' in stream:
        protocol = stream['protocol']

    stream_modes = [ 'default' ]
    if latency_only:
        if latency:
            stream_modes = [ 'latency' ]
        else:
            stream_modes = []
    elif latency:
        stream_modes.append('latency')

    for stream_mode in stream_modes:
        stream_packet = None
        flow_stats = None
        stream_pg_id = None
        stream_control = None

        if frame_type == 'bulk':
            stream_packet = create_pkt(stream['frame_size'],
                                       device_pair[direction]['packet_values']['macs']['src'],
                                       device_pair[direction]['packet_values']['macs']['dst'],
                                       device_pair[direction]['packet_values']['ips']['src'],
                                       device_pair[direction]['packet_values']['ips']['dst'],
                                       device_pair[direction]['packet_values']['ports']['src'],
                                       device_pair[direction]['packet_values']['ports']['dst'],
                                       protocol,
                                       device_pair[direction]['packet_values']['vlan'],
                                       stream['flow_mods'],
                                       stream['flows'],
                                       t_global.args.enable_flow_cache)
        else:
            raise ValueError("Invalid frame_type: %s" % (frame_type))

        stream_pg_id = device_pair[direction]['pg_ids'][stream_mode]['start_index'] + (device_pair[direction]['pg_ids'][stream_mode]['total'] - device_pair[direction]['pg_ids'][stream_mode]['available'])
        device_pair[direction]['pg_ids'][stream_mode]['available'] -= 1
        if stream_mode == 'default':
            flow_stats = STLFlowStats(pg_id = stream_pg_id)
        elif stream_mode == 'latency':
            flow_stats = STLFlowLatencyStats(pg_id = stream_pg_id)

        stream_rate = stream['rate']
        if 'latency' in stream_modes and stream_mode == 'default':
            stream_rate -= t_global.args.latency_rate
        elif stream_mode == 'latency':
            stream_rate = t_global.args.latency_rate

        stream_total_pkts = int(stream_rate * t_global.args.runtime)
        stream_control = STLTXSingleBurst(pps = stream_rate, total_pkts = stream_total_pkts)

        for stream_type in stream_types:
            stream_name = "%s-stream-%d" % (stream_type, stream_pg_id)

            if stream_type == 'measurement':
                device_pair[direction]['traffic_profile'][stream_mode]['protocol'].append(protocol)
                device_pair[direction]['traffic_profile'][stream_mode]['pps'].append(stream_rate)
                device_pair[direction]['traffic_profile'][stream_mode]['pg_ids'].append(stream_pg_id)
                device_pair[direction]['traffic_profile'][stream_mode]['names'].append(stream_name)
                device_pair[direction]['traffic_profile'][stream_mode]['next_stream_names'].append(None)
                device_pair[direction]['traffic_profile'][stream_mode]['frame_sizes'].append(stream['frame_size'])
                device_pair[direction]['traffic_profile'][stream_mode]['traffic_shares'].append(1.0)
                device_pair[direction]['traffic_profile'][stream_mode]['self_starts'].append(True)
                device_pair[direction]['traffic_profile'][stream_mode]['run_time'].append(t_global.args.runtime)
                device_pair[direction]['traffic_profile'][stream_mode]['stream_modes'].append('burst')

                device_pair[direction]['traffic_streams'].append(STLStream(packet = stream_packet,
                                                                           flow_stats = flow_stats,
                                                                           mode = stream_control,
                                                                           name = stream_name,
                                                                           next = None,
                                                                           self_start = True))
            elif stream_type == 'teaching_warmup':
                foo = 2
            elif stream_type == 'teaching_measurement':
                foo = 3
            else:
                raise ValueError("Invalid stream_type: %s" % (stream_type))

    return

def main():
    process_options()

    traffic_profile = {}

    forward_direction = '->'
    reverse_direction = '<-'
    both_directions = "%s%s" % (reverse_direction, forward_direction)

    directions = [ forward_direction, reverse_direction ]

    packet_values = { 'ports': { 'src': 32768,
                                 'dst': 53 },
                      'macs':  { 'src': None,
                                 'dst': None },
                      'ips':   { 'src': None,
                                 'dst': None },
                      'vlan': None }

    pg_id_values = { "default": { 'available':   None,
                                  'total':       None,
                                  'start_index': None,
                                  'list':        [] },
                     "latency": { 'available':   None,
                                  'total':       None,
                                  'start_index': None,
                                  'list':        [] } }

    stream_profile_object = { 'protocol': [],
                              'pps': [],
                              'pg_ids': [],
                              'names': [],
                              'next_stream_names': [],
                              'frame_sizes': [],
                              'traffic_shares': [],
                              'self_starts': [],
                              'run_time': [],
                              'stream_modes': [] }

    claimed_device_pairs = []
    for device_pair in t_global.args.device_pairs.split(','):
         ports = device_pair.split(':')
         port_a = int(ports[0])
         port_b = int(ports[1])
         claimed_device_pairs.extend([port_a, port_b])

    all_ports = []
    measurement_ports = []
    teaching_ports = []

    device_pairs = []
    for device_pair in t_global.args.active_device_pairs.split(','):
         ports = device_pair.split(':')
         port_a = int(ports[0])
         port_b = int(ports[1])
         string = ''
         if t_global.args.traffic_direction == 'bidirec':
              string = "%s" % (both_directions)
         elif t_global.args.traffic_direction == 'unidirec':
              string = "%s" % (forward_direction)
         elif t_global.args.traffic_direction == 'revunidirec':
              string = "%s" % (reverse_direction)

         myprint("Configuring device pair: %d%s%d" % (port_a, string, port_b))
         all_ports.extend([port_a, port_b])

         if t_global.args.traffic_direction == 'bidirec':
             measurement_ports.extend([port_a, port_b])

             if t_global.args.send_teaching_warmup or t_global.args.send_teaching_measurement:
                 teaching_ports.extend([port_a, port_b])
         elif t_global.args.traffic_direction == 'unidirec':
             measurement_ports.append(port_a)

             if t_global.args.send_teaching_warmup or t_global.args.send_teaching_measurement:
                 teaching_ports.append(port_b)
         elif t_global.args.traffic_direction == 'revunidirec':
             measurement_ports.append(port_b)

             if t_global.args.send_teaching_warmup or t_global.args.send_teaching_measurement:
                 teaching_ports.append(port_a)

         device_pairs.append({ forward_direction: { 'ports': { 'tx': port_a,
                                                               'rx': port_b },
                                                    'id_string': "%s%s%s" % (port_a, forward_direction, port_b),
                                                    'packet_values': copy.deepcopy(packet_values),
                                                    'pg_ids': copy.deepcopy(pg_id_values),
                                                    'traffic_profile': { 'default': copy.deepcopy(stream_profile_object),
                                                                         'latency': copy.deepcopy(stream_profile_object) },
                                                    'traffic_streams': [],
                                                    'teaching_warmup_traffic_streams': [],
                                                    'teaching_measurement_traffic_streams': [],
                                                    'active': False },
                               reverse_direction: { 'ports': { 'tx': port_b,
                                                               'rx': port_a },
                                                    'id_string': "%s%s%s" % (port_a, reverse_direction, port_b),
                                                    'packet_values': copy.deepcopy(packet_values),
                                                    'pg_ids': copy.deepcopy(pg_id_values),
                                                    'traffic_profile': { 'default': copy.deepcopy(stream_profile_object),
                                                                         'latency': copy.deepcopy(stream_profile_object) },
                                                    'traffic_streams': [],
                                                    'teaching_warmup_traffic_streams': [],
                                                    'teaching_measurement_traffic_streams': [],
                                                    'active': False },
                               'max_default_pg_ids': 0,
                               'max_latency_pg_ids': 0,
                               'device_pair': device_pair })

    stats = 0
    return_value = 1

    try:
        traffic_profile_fp = open(t_global.args.traffic_profile, 'r')
        traffic_profile = load_traffic_profile(json.load(traffic_profile_fp))
        traffic_profile_fp.close()
        myprint("TRAFFIC PROFILE:")
        myprint(dump_json_readable(traffic_profile))

        if len(traffic_profile['streams']) == 0:
            raise ValueError("There are no streams in the loaded traffic profile")
    except:
        myprint("EXCEPTION: %s" % traceback.format_exc())
        myprint("ERROR: Could not load a valid traffic profile from %s" % t_global.args.traffic_profile)
        return return_value

    c = STLClient()

    active_ports = 0
    if t_global.args.traffic_direction == 'bidirec':
         active_ports = len(all_ports)

         for device_pair in device_pairs:
              device_pair[forward_direction]['active'] = True
              device_pair[reverse_direction]['active'] = True
    else:
         active_ports = len(all_ports) / 2

         for device_pair in device_pairs:
              if t_global.args.traffic_direction == 'unidirec':
                   device_pair[forward_direction]['active'] = True
              elif t_global.args.traffic_direction == 'revunidirec':
                   device_pair[reverse_direction]['active'] = True

    myprint("Active TX Ports: %d" % active_ports)

    try:
        if t_global.args.debug:
             # turn this on for some information
             c.set_verbose("high")

        # connect to server
        myprint("Establishing connection to TRex server...")
        c.connect()
        myprint("Connection established")

        # prepare our ports
        c.acquire(ports = claimed_device_pairs, force=True)
        c.reset(ports = claimed_device_pairs)
        c.set_port_attr(ports = all_ports, promiscuous = True)

        port_info = c.get_port_info(ports = claimed_device_pairs)
        myprint("READABLE PORT INFO:", stderr_only = True)
        myprint(dump_json_readable(port_info), stderr_only = True)
        myprint("PARSABLE PORT INFO: %s" % dump_json_parsable(port_info), stderr_only = True)

        port_speed_verification_fail = False

        for device_pair in device_pairs:
            for direction in directions:
                other_direction = ''
                if direction == forward_direction:
                    other_direction = reverse_direction
                else:
                    other_direction = forward_direction

                if port_info[device_pair[direction]['ports']['tx']]['speed'] == 0:
                    port_speed_verification_fail = True
                    myprint("ERROR: Device with port index = %d failed speed verification test" % device_pair[direction]['ports']['tx'])

                device_pair[direction]['packet_values']['macs']['src'] = port_info[device_pair[direction]['ports']['tx']]['src_mac']
                device_pair[direction]['packet_values']['macs']['dst'] = port_info[device_pair[direction]['ports']['rx']]['src_mac']

                if port_info[device_pair[direction]['ports']['tx']]['src_ipv4'] != "-":
                    device_pair[direction]['packet_values']['ips']['src'] = port_info[device_pair[direction]['ports']['tx']]['src_ipv4']
                    device_pair[other_direction]['packet_values']['ips']['dst'] = port_info[device_pair[direction]['ports']['tx']]['src_ipv4']
                else:
                    ip_address = "%d.%d.%d.%d" % (device_pair[direction]['ports']['tx']+1, device_pair[direction]['ports']['tx']+1, device_pair[direction]['ports']['tx']+1, device_pair[direction]['ports']['tx']+1)
                    device_pair[direction]['packet_values']['ips']['src'] = ip_address
                    device_pair[other_direction]['packet_values']['ips']['dst'] = ip_address

        if port_speed_verification_fail:
             raise RuntimeError("Failed port speed verification")

        for device_pair in device_pairs:
             if t_global.args.traffic_direction == 'bidirec':
                  if port_info[device_pair[forward_direction]['ports']['tx']]["rx"]["counters"] <= port_info[device_pair[forward_direction]['ports']['rx']]["rx"]["counters"]:
                       device_pair['max_default_pg_ids'] = port_info[device_pair[forward_direction]['ports']['tx']]["rx"]["counters"] / len(device_pairs)
                  else:
                       device_pair['max_default_pg_ids'] = port_info[device_pair[forward_direction]['ports']['rx']]["rx"]["counters"] / len(device_pairs)
             else:
                  if t_global.args.traffic_direction == 'unidirec':
                       device_pair['max_default_pg_ids'] = port_info[device_pair[forward_direction]['ports']['rx']]["rx"]["counters"] / len(device_pairs)
                  elif t_global.args.traffic_direction == 'revunidirec':
                       device_pair['max_default_pg_ids'] = port_info[device_pair[reverse_direction]['ports']['rx']]["rx"]["counters"] / len(device_pairs)

             device_pair['max_latency_pg_ids'] = 128 / len(device_pairs) # 128 is the maximum number of software counters for latency in TRex

        pg_id_base = 1
        for device_pair in device_pairs:
             if not t_global.args.traffic_direction == 'bidirec':
                  direction = forward_direction
                  if t_global.args.traffic_direction == 'revunidirec':
                       direction = reverse_direction

                  device_pair[direction]['pg_ids']['default']['total']       = device_pair['max_default_pg_ids']
                  device_pair[direction]['pg_ids']['default']['available']   = device_pair[direction]['pg_ids']['default']['total']
                  device_pair[direction]['pg_ids']['default']['start_index'] = pg_id_base
                  device_pair[direction]['pg_ids']['latency']['total']       = device_pair['max_latency_pg_ids']
                  device_pair[direction]['pg_ids']['latency']['available']   = device_pair[direction]['pg_ids']['latency']['total']
                  device_pair[direction]['pg_ids']['latency']['start_index'] = device_pair[direction]['pg_ids']['default']['start_index'] + device_pair[direction]['pg_ids']['default']['total']
             else:
                  device_pair[forward_direction]['pg_ids']['default']['total']       = device_pair['max_default_pg_ids'] / 2
                  device_pair[forward_direction]['pg_ids']['default']['available']   = device_pair[forward_direction]['pg_ids']['default']['total']
                  device_pair[forward_direction]['pg_ids']['default']['start_index'] = pg_id_base
                  device_pair[forward_direction]['pg_ids']['latency']['total']       = device_pair['max_latency_pg_ids'] / 2
                  device_pair[forward_direction]['pg_ids']['latency']['available']   = device_pair[forward_direction]['pg_ids']['latency']['total']
                  device_pair[forward_direction]['pg_ids']['latency']['start_index'] = device_pair[forward_direction]['pg_ids']['default']['start_index'] + device_pair[forward_direction]['pg_ids']['default']['total']

                  device_pair[reverse_direction]['pg_ids']['default']['total']       = device_pair[forward_direction]['pg_ids']['default']['total']
                  device_pair[reverse_direction]['pg_ids']['default']['available']   = device_pair[reverse_direction]['pg_ids']['default']['total']
                  device_pair[reverse_direction]['pg_ids']['default']['start_index'] = device_pair[forward_direction]['pg_ids']['default']['start_index'] + device_pair[forward_direction]['pg_ids']['default']['total'] + device_pair[forward_direction]['pg_ids']['latency']['total']
                  device_pair[reverse_direction]['pg_ids']['latency']['total']       = device_pair[forward_direction]['pg_ids']['latency']['total']
                  device_pair[reverse_direction]['pg_ids']['latency']['available']   = device_pair[reverse_direction]['pg_ids']['latency']['total']
                  device_pair[reverse_direction]['pg_ids']['latency']['start_index'] = device_pair[reverse_direction]['pg_ids']['default']['start_index'] + device_pair[reverse_direction]['pg_ids']['default']['total']

             pg_id_base = pg_id_base + device_pair['max_default_pg_ids'] + device_pair['max_latency_pg_ids']

        myprint("Creating Streams from loaded traffic profile")
        for device_pair in device_pairs:
            for stream in traffic_profile['streams']:
                if t_global.args.traffic_direction == 'bidirec' or t_global.args.traffic_direction == 'unidirec':
                    if not 'direction' in stream or stream['direction'] == forward_direction or stream['direction'] == both_directions:
                        create_stream(stream, device_pair, forward_direction, reverse_direction)

                if t_global.args.traffic_direction == 'bidirec' or t_global.args.traffic_direction == 'revunidirec':
                    if not 'direction' in stream or stream['direction'] == reverse_direction or stream['direction'] == both_directions:
                        create_stream(stream, device_pair, reverse_direction, forward_direction)

            for direction in directions:
                if len(device_pair[direction]['traffic_streams']):
                    myprint("DEVICE PAIR %s | READABLE STREAMS FOR DIRECTION '%s':" % (device_pair['device_pair'], device_pair[direction]['id_string']), stderr_only = True)
                    myprint(dump_json_readable(device_pair[direction]['traffic_profile']), stderr_only = True)
                    myprint("DEVICE PAIR %s | PARSABLE STREAMS FOR DIRECTION '%s': %s" % (device_pair['device_pair'], device_pair[direction]['id_string'], dump_json_parsable(device_pair[direction]['traffic_profile'])), stderr_only = True)

        if t_global.args.send_teaching_warmup:
             for device_pair in device_pairs:
                  for direction in directions:
                       if len(device_pair[direction]['teaching_warmup_traffic_streams']):
                            myprint("Adding teaching warmup stream(s) for device pair '%s' to port %d" % (device_pair['device_pair'], device_pair[direction]['ports']['tx']))
                            c.add_streams(streams = device_pair[direction]['teaching_warmup_traffic_streams'], ports = device_pair[direction]['ports']['tx'])

             myprint("Transmitting teaching warmup packets...")
             start_time = datetime.datetime.now()

             warmup_timeout = int(max(30.0, (float(t_global.args.num_flows) / t_global.args.teaching_warmup_packet_rate) * 1.05))

             warmup_ports = teaching_ports

             try:
                  c.start(ports = warmup_ports, force = True)
                  c.wait_on_traffic(ports = warmup_ports, timeout = warmup_timeout)

                  stop_time = datetime.datetime.now()
                  total_time = stop_time - start_time
                  myprint("...teaching warmup transmission complete -- %d total second(s) elapsed" % total_time.total_seconds())
             except STLTimeoutError as e:
                  c.stop(ports = warmup_ports)
                  stop_time = datetime.datetime.now()
                  total_time = stop_time - start_time
                  myprint("...TIMEOUT ERROR: The teaching warmup did not end on it's own correctly within the allotted time (%d seconds) -- %d total second(s) elapsed" % (warmup_timeout, total_time.total_seconds()))
                  return return_value
             except STLError as e:
                  c.stop(ports = warmup_ports)
                  myprint("...ERROR: wait_on_traffic: STLError: %s" % e)
                  return return_value

             c.reset(ports = warmup_ports)
             c.set_port_attr(ports = warmup_ports, promiscuous = True)

        run_ports = []

        for device_pair in device_pairs:
             for direction in directions:
                  port_streams = 0

                  if len(device_pair[direction]['traffic_streams']):
                       myprint("Adding stream(s) for device pair '%s' to port %d" % (device_pair['device_pair'], device_pair[direction]['ports']['tx']))
                       port_streams += len(device_pair[direction]['traffic_streams'])
                       c.add_streams(streams = device_pair[direction]['traffic_streams'], ports = device_pair[direction]['ports']['tx'])

                  if t_global.args.send_teaching_measurement and len(device_pair[direction]['teaching_measurement_traffic_streams']):
                       myprint("Adding teaching stream(s) for device pair '%s' to port %d" % (device_pair['device_pair'], device_pair[direction]['ports']['tx']))
                       port_streams += len(device_pair[direction]['teaching_measurement_traffic_streams'])
                       c.add_streams(streams = device_pair[direction]['teaching_measurement_traffic_streams'], ports = device_pair[direction]['ports']['tx'])

                  if port_streams:
                       run_ports.append(device_pair[direction]['ports']['tx'])

        myprint("DEVICE PAIR INFORMATION:", stderr_only = True)
        myprint(dump_json_readable(device_pairs), stderr_only = True)
        myprint("DEVICE PAIR INFORMATION: %s" % dump_json_parsable(device_pairs), stderr_only = True)

        # clear the event log
        c.clear_events()

        # clear the stats
        c.clear_stats(ports = all_ports)

        # log start of test
        timeout_seconds = math.ceil(float(t_global.args.runtime) * (1 + (float(t_global.args.runtime_tolerance) / 100)))
        stop_time = datetime.datetime.now()
        start_time = datetime.datetime.now()
        myprint("Starting test at %s" % start_time.strftime("%H:%M:%S on %Y-%m-%d"))
        expected_end_time = start_time + datetime.timedelta(seconds = t_global.args.runtime)
        expected_timeout_time = start_time + datetime.timedelta(seconds = timeout_seconds)
        myprint("The test should end at %s" % expected_end_time.strftime("%H:%M:%S on %Y-%m-%d"))
        myprint("The test will timeout with an error at %s" % expected_timeout_time.strftime("%H:%M:%S on %Y-%m-%d"))

        for device_pair in device_pairs:
             for direction in directions:
                  if device_pair[direction]['active']:
                       myprint("Transmitting from port %d to port %d for %d seconds..." % (device_pair[direction]['ports']['tx'], device_pair[direction]['ports']['rx'], t_global.args.runtime))

        # start the traffic
        c.start(ports = run_ports, force = True, duration = t_global.args.runtime, total = False, core_mask = STLClient.CORE_MASK_PIN)

        timeout = False
        force_quit = False

        try:
             myprint("Waiting...")
             c.wait_on_traffic(ports = run_ports, timeout = timeout_seconds)
             stop_time = datetime.datetime.now()
        except STLTimeoutError as e:
             c.stop(ports = run_ports)
             stop_time = datetime.datetime.now()
             myprint("TIMEOUT ERROR: The test did not end on it's own correctly within the allotted time.")
             timeout = True
        except STLError as e:
             c.stop(ports = run_ports)
             stop_time = datetime.datetime.now()
             myprint("ERROR: wait_on_traffic: STLError: %s" % e)
             force_quit = True

        # log end of test
        myprint("Finished test at %s" % stop_time.strftime("%H:%M:%S on %Y-%m-%d"))
        total_time = stop_time - start_time
        myprint("Test ran for %d seconds (%s)" % (total_time.total_seconds(), total_time))

        stats = c.get_stats(sync_now = True)
        stats["global"]["runtime"] = total_time.total_seconds()
        stats["global"]["timeout"] = timeout
        stats["global"]["force_quit"] = force_quit
        stats["global"]["early_exit"] = False

        for device_pair in device_pairs:
             for flows_index, flows_id in enumerate(stats["flow_stats"]):
                  if flows_id == "global":
                       continue

                  if not int(flows_id) in device_pair[forward_direction]['traffic_profile']['default']['pg_ids'] and not int(flows_id) in device_pair[reverse_direction]['traffic_profile']['default']['pg_ids'] and not int(flows_id) in device_pair[forward_direction]['traffic_profile']['latency']['pg_ids'] and not int(flows_id) in device_pair[reverse_direction]['traffic_profile']['latency']['pg_ids']:
                       continue

                  flow_tx = 0
                  flow_rx = 0

                  if not "loss" in stats["flow_stats"][flows_id]:
                       stats["flow_stats"][flows_id]["loss"] = dict()
                       stats["flow_stats"][flows_id]["loss"]["pct"] = dict()
                       stats["flow_stats"][flows_id]["loss"]["cnt"] = dict()

                  for direction in directions:
                       if device_pair[direction]['ports']['tx'] in stats["flow_stats"][flows_id]["tx_pkts"] and device_pair[direction]['ports']['rx'] in stats["flow_stats"][flows_id]["rx_pkts"] and stats["flow_stats"][flows_id]["tx_pkts"][device_pair[direction]['ports']['tx']]:
                            stats["flow_stats"][flows_id]["loss"]["pct"][device_pair[direction]['id_string']] = (1 - (float(stats["flow_stats"][flows_id]["rx_pkts"][device_pair[direction]['ports']['rx']]) / float(stats["flow_stats"][flows_id]["tx_pkts"][device_pair[direction]['ports']['tx']]))) * 100
                            stats["flow_stats"][flows_id]["loss"]["cnt"][device_pair[direction]['id_string']] = float(stats["flow_stats"][flows_id]["tx_pkts"][device_pair[direction]['ports']['tx']]) - float(stats["flow_stats"][flows_id]["rx_pkts"][device_pair[direction]['ports']['rx']])
                            flow_tx += stats["flow_stats"][flows_id]["tx_pkts"][device_pair[direction]['ports']['tx']]
                            flow_rx += stats["flow_stats"][flows_id]["rx_pkts"][device_pair[direction]['ports']['rx']]
                       else:
                            stats["flow_stats"][flows_id]["loss"]["pct"][device_pair[direction]['id_string']] = "N/A"
                            stats["flow_stats"][flows_id]["loss"]["cnt"][device_pair[direction]['id_string']] = "N/A"

                  if flow_tx:
                       stats["flow_stats"][flows_id]["loss"]["pct"]["total"] = (1 - (float(flow_rx) / float(flow_tx))) * 100
                       stats["flow_stats"][flows_id]["loss"]["cnt"]["total"] = float(flow_tx) - float(flow_rx)
                  else:
                       stats["flow_stats"][flows_id]["loss"]["pct"]["total"] = "N/A"
                       stats["flow_stats"][flows_id]["loss"]["cnt"]["total"] = "N/A"

        warning_events = c.get_warnings()
        if len(warning_events):
             myprint("TRex Warning Events:")
             for warning in warning_events:
                  myprint("    WARNING: %s" % warning)

        events = c.get_events()
        if len(events):
             myprint("TRex Events:")
             for event in events:
                  myprint("    EVENT: %s" % event)

        myprint("TX Utilization: %f%%" % stats['global']['cpu_util'])
        myprint("RX Utilization: %f%%" % stats['global']['rx_cpu_util'])
        myprint("TX Queue Full:  %d"   % stats['global']['queue_full'])

        myprint("READABLE RESULT:", stderr_only = True)
        myprint(dump_json_readable(stats), stderr_only = True)
        myprint("PARSABLE RESULT: %s" % dump_json_parsable(stats), stderr_only = True)

        return_value = 0

    except STLError as e:
        myprint("STLERROR: %s" % e)

    except (ValueError, RuntimeError) as e:
        myprint("ERROR: %s" % e)

    except:
        myprint("EXCEPTION: %s" % traceback.format_exc())

    finally:
        myprint("Disconnecting from TRex server...")
        c.disconnect()
        myprint("Connection severed")
        return return_value

if __name__ == "__main__":
    exit(main())
