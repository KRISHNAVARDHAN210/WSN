import random
from enum import Enum
import sys
sys.path.insert(1, '.')
from source import wsnlab_vis as wsn
import math
from source import config
from source.addressing import next_short_addr
from collections import Counter


import csv  # <— add this near your other imports

# Track where each node is placed
NODE_POS = {}  # {node_id: (x, y)}

# --- tracking containers ---
ALL_NODES = []              # node objects
CLUSTER_HEADS = []
ROLE_COUNTS = Counter()     # live tally per Roles enum

def _addr_str(a): return "" if a is None else str(a)
def _role_name(r): return r.name if hasattr(r, "name") else str(r)


Roles = Enum('Roles', 'UNDISCOVERED UNREGISTERED ROOT REGISTERED CLUSTER_HEAD')
"""Enumeration of roles"""

###########################################################
class SensorNode(wsn.Node):
    """SensorNode class is inherited from Node class in wsnlab.py.
    It will run data collection tree construction algorithms.

    Attributes:
        role (Roles): role of node
        is_root_eligible (bool): keeps eligibility to be root
        c_probe (int): probe message counter
        th_probe (int): probe message threshold
        neighbors_table (Dict): keeps the neighbor information with received heart beat messages
    """

    ###################
    def init(self):
        self.scene.nodecolor(self.id, 1, 1, 1)
        self.sleep()
        self.ch_addr = None
        self.parent_gui = None
        self.root_addr = None

        if (self.id) == config.ROOT_ID:
            self.addr = wsn.Addr(config.NETWORK_ID, config.ROOT_SHORT_ADDR)
            self.root_addr = self.addr
            self.ch_addr   = self.addr
            self.set_role(Roles.ROOT)
            self.hop_count = 0
            self.scene.nodecolor(self.id, 0, 0, 0)
            print(f"[ROOT] Node {self.id} initialized as ROOT with addr {self.addr}")
        else:
            self.addr = next_short_addr()  # <-- already a wsn.Addr
            self.set_role(Roles.UNDISCOVERED)
            self.hop_count = 99999
            print(f"[NODE] Node {self.id} initialized with addr {self.addr}")

        self.is_root_eligible = ((self.id) == config.ROOT_ID)
        self.c_probe = 0
        self.th_probe = 10
        self.neighbors_table = {}
        self.candidate_parents_table = []
        self.child_networks_table = {}
        self.members_table = []          
        self.received_JR_guis = []
        self.multi_neighbors = {}      
        self._share_seq = 0           
        self._seen_share = {}         
        if getattr(config, "NEIGHBOR_SHARE_MAX_HOPS", 1) > 1:
         self.set_timer('TIMER_NEIGHBOR_SHARE', config.NEIGHBOR_SHARE_INTERVAL)

    ###################
    def run(self):
        """Setting the arrival timer to wake up after firing.

        Args:

        Returns:

        """
        self.set_timer('TIMER_ARRIVAL', self.arrival)

    ###################

    def set_role(self, new_role, *, recolor=True):
        """Central place to switch roles, keep tallies, and (optionally) recolor."""
        old_role = getattr(self, "role", None)
        if old_role is not None:
            ROLE_COUNTS[old_role] -= 1
            if ROLE_COUNTS[old_role] <= 0:
                ROLE_COUNTS.pop(old_role, None)
        ROLE_COUNTS[new_role] += 1
        self.role = new_role
        try:
            self.kill_timer('TIMER_HEART_BEAT')
        except Exception:
            pass

        if new_role in (Roles.ROOT, Roles.CLUSTER_HEAD):
            self.set_timer('TIMER_HEART_BEAT', config.HEARTH_BEAT_TIME_INTERVAL)

        if recolor:
            if new_role == Roles.UNDISCOVERED:
                self.scene.nodecolor(self.id, 1, 1, 1)
            elif new_role == Roles.UNREGISTERED:
                self.scene.nodecolor(self.id, 1, 1, 0)
            elif new_role == Roles.REGISTERED:
                self.scene.nodecolor(self.id, 0, 1, 0)
            elif new_role == Roles.CLUSTER_HEAD:
                self.scene.nodecolor(self.id, 0, 0, 1)
                self.draw_tx_range()
            elif new_role == Roles.ROOT:
                self.scene.nodecolor(self.id, 0, 0, 0)
                self.set_timer('TIMER_EXPORT_CH_CSV', config.EXPORT_CH_CSV_INTERVAL)
                self.set_timer('TIMER_EXPORT_NEIGHBOR_CSV', config.EXPORT_NEIGHBOR_CSV_INTERVAL)
                self.set_timer('TIMER_EXPORT_MULTIHOP_CSV', config.EXPORT_NEIGHBOR_CSV_INTERVAL)

    def become_unregistered(self):
        if self.role != Roles.UNDISCOVERED:
            self.kill_all_timers()
            self.log('I became UNREGISTERED')
        self.scene.nodecolor(self.id, 1, 1, 0)
        self.erase_parent()
        self.addr = None
        self.ch_addr = None
        self.parent_gui = None
        self.root_addr = None
        self.set_role(Roles.UNREGISTERED)
        self.c_probe = 0
        self.th_probe = 10
        self.hop_count = 99999
        self.neighbors_table = {}
        self.candidate_parents_table = []
        self.child_networks_table = {}
        self.members_table = []
        self.received_JR_guis = []  # keeps received Join Request global unique ids
        self.send_probe()
        self.set_timer('TIMER_JOIN_REQUEST', 20)

    ###################
    def update_neighbor(self, pck):
        pck['arrival_time'] = self.now
        # compute Euclidean distance between self and neighbor
        if pck['gui'] in NODE_POS and self.id in NODE_POS:
            x1, y1 = NODE_POS[self.id]
            x2, y2 = NODE_POS[pck['gui']]
            pck['distance'] = math.hypot(x1 - x2, y1 - y2)
        self.neighbors_table[pck['gui']] = pck

        if pck['gui'] not in self.child_networks_table.keys() or pck['gui'] not in self.members_table:
            if pck['gui'] not in self.candidate_parents_table:
                self.candidate_parents_table.append(pck['gui'])

    ###################
    def select_and_join(self):
        min_hop = 99999
        min_hop_gui = 99999
        for gui in self.candidate_parents_table:
            if self.neighbors_table[gui]['hop_count'] < min_hop or (self.neighbors_table[gui]['hop_count'] == min_hop and gui < min_hop_gui):
                min_hop = self.neighbors_table[gui]['hop_count']
                min_hop_gui = gui
        selected_addr = self.neighbors_table[min_hop_gui]['source']
        self.send_join_request(selected_addr)
        self.set_timer('TIMER_JOIN_REQUEST', 5)


    ###################
    def send_probe(self):
        """Sending probe message to be discovered and registered.

        Args:

        Returns:

        """
        self.send({'dest': wsn.BROADCAST_ADDR, 'type': 'PROBE'})

    ###################
    def send_heart_beat(self):
        """Sending heart beat message

        Args:

        Returns:

        """
        self.send({'dest': wsn.BROADCAST_ADDR,
                   'type': 'HEART_BEAT',
                   'source': self.ch_addr if self.ch_addr is not None else self.addr,
                   'gui': self.id,
                   'role': self.role,
                   'addr': self.addr,
                   'ch_addr': self.ch_addr,
                   'hop_count': self.hop_count})

    ###################
    def send_join_request(self, dest):
        """Sending join request message to given destination address to join destination network

        Args:
            dest (Addr): Address of destination node
        Returns:

        """
        self.send({'dest': dest, 'type': 'JOIN_REQUEST', 'gui': self.id})

    ###################
    def send_join_reply(self, gui, addr):
        """Sending join reply message to register the node requested to join.
        The message includes a gui to determine which node will take this reply, an addr to be assigned to the node
        and a root_addr.

        Args:
            gui (int): Global unique ID
            addr (Addr): Address that will be assigned to new registered node
        Returns:

        """
        self.send({'dest': wsn.BROADCAST_ADDR, 'type': 'JOIN_REPLY', 'source': self.ch_addr,
                   'gui': self.id, 'dest_gui': gui, 'addr': addr, 'root_addr': self.root_addr,
                   'hop_count': self.hop_count+1})

    ###################
    def send_join_ack(self, dest):
        """Sending join acknowledgement message to given destination address.

        Args:
            dest (Addr): Address of destination node
        Returns:

        """
        self.send({'dest': dest, 'type': 'JOIN_ACK', 'source': self.addr,
                   'gui': self.id})

    ###################
    def _now(self):  # tiny helper
        return getattr(self, "now", 0)
    
    def build_neighbor_summary(self):
        """
        Return our current view of neighbors up to K hops:
        - 1-hop neighbors from neighbors_table as hop=1
        - multi-hop entries from self.multi_neighbors as their stored hop
        This is our distance-to-target. Receiver will add +1.
        """
        summary = {}
        for n_gui, p in self.neighbors_table.items():
            summary[n_gui] = 1
        for t_gui, meta in self.multi_neighbors.items():
            h = meta.get("hop", 99999)
            if h <= config.NEIGHBOR_SHARE_MAX_HOPS:
                if t_gui not in summary or h < summary[t_gui]:
                    summary[t_gui] = h
        summary.pop(self.id, None)
        return [{"gui": gid, "hop": hop} for gid, hop in summary.items()]
    
    def send_neighbor_share(self):
        """Broadcast our neighbor summary periodically."""
        self._share_seq += 1
        payload = {
            "dest": wsn.BROADCAST_ADDR,
            "type": "NEIGHBOR_SHARE",
            "from_gui": self.id,
            "seq": self._share_seq,
            "items": self.build_neighbor_summary(),  
        }
        self.send(payload)

    def merge_neighbor_share(self, pck):
        """
        Merge a received NEIGHBOR_SHARE into our multi-hop table.
        The sender’s hop value is distance from sender; for us it's +1.
        We track the best (smallest-hop) path and 'via' (the sender).
        """
        sender = pck.get("from_gui")
        if sender is None:
            return

        # dedupe on sequence per sender
        seq = pck.get("seq")
        last = self._seen_share.get(sender, -1)
        if seq is not None and seq <= last:
            return
        self._seen_share[sender] = seq

        items = pck.get("items", [])
        now = self._now()

        for it in items:
            tgt = it.get("gui")
            h_sender = it.get("hop", 99999)
            if tgt is None:
                continue
            if tgt == self.id:
                continue  # ignore ourselves

            # our distance = sender's distance + 1
            h_here = h_sender + 1
            if h_here > config.NEIGHBOR_SHARE_MAX_HOPS:
                continue

            best = self.multi_neighbors.get(tgt)
            if best is None or h_here < best["hop"] or (h_here == best["hop"] and best.get("via", 1e9) > sender):
                self.multi_neighbors[tgt] = {"hop": h_here, "via": sender, "last_seen": now}

    def cleanup_stale_neighbors(self):
        """Drop stale multi-hop entries that haven't been refreshed recently."""
        now = self._now()
        stale = []
        for tgt, meta in self.multi_neighbors.items():
            if (now - meta.get("last_seen", 0)) > config.NEIGHBOR_STALE_TIME:
                stale.append(tgt)
        for tgt in stale:
            self.multi_neighbors.pop(tgt, None)                
    
    def route_and_forward_package(self, pck):
        """Routing and forwarding given package

        Args:
            pck (Dict): package to route and forward it should contain dest, source and type.
        Returns:

        """
        if self.role != Roles.ROOT:
            pck['next_hop'] = self.neighbors_table[self.parent_gui]['ch_addr']
        if self.ch_addr is not None:
            if pck['dest'].net_addr == self.ch_addr.net_addr:
                pck['next_hop'] = pck['dest']
            else:
                for child_gui, child_networks in self.child_networks_table.items():
                    if pck['dest'].net_addr in child_networks:
                        pck['next_hop'] = self.neighbors_table[child_gui]['addr']
                        break

        self.send(pck)

    ###################
    def send_network_request(self):
        """Sending network request message to root address to be cluster head

        Args:

        Returns:

        """
        self.route_and_forward_package({'dest': self.root_addr, 'type': 'NETWORK_REQUEST', 'source': self.addr})

    ###################
    def send_network_reply(self, dest, addr):
        """Sending network reply message to dest address to be cluster head with a new adress

        Args:
            dest (Addr): destination address
            addr (Addr): cluster head address of new network

        Returns:

        """
        self.route_and_forward_package({'dest': dest, 'type': 'NETWORK_REPLY', 'source': self.addr, 'addr': addr})

    ###################
    def send_network_update(self):
        """Sending network update message to parent

        Args:

        Returns:

        """
        child_networks = [self.ch_addr.net_addr]
        for networks in self.child_networks_table.values():
            child_networks.extend(networks)

        self.send({'dest': self.neighbors_table[self.parent_gui]['ch_addr'], 'type': 'NETWORK_UPDATE', 'source': self.addr,
                   'gui': self.id, 'child_networks': child_networks})

    ###################
    def on_receive(self, pck):
        """Executes when a package received.

        Args:
            pck (Dict): received package
        Returns:

        """
        if pck.get("type") == "NEIGHBOR_SHARE":
            # First, ensure we consider the sender as a 1-hop neighbor if we see their share
                if "from_gui" in pck:
                    gui_sender = pck["from_gui"]
                    if gui_sender not in self.neighbors_table and self.id in NODE_POS and gui_sender in NODE_POS:
                        # create a minimal 1-hop record (optional, helps bootstrapping)
                        x1, y1 = NODE_POS[self.id]
                        x2, y2 = NODE_POS[gui_sender]
                        self.neighbors_table[gui_sender] = {
                            "type": "HEART_BEAT",
                            "gui": gui_sender,
                            "addr": None,
                            "ch_addr": None,
                            "hop_count": 1e6,  # unknown
                            "arrival_time": self.now,
                            "distance": math.hypot(x1 - x2, y1 - y2),
                        }
                self.merge_neighbor_share(pck)
                return
        if self.role == Roles.ROOT or self.role == Roles.CLUSTER_HEAD:  # if the node is root or cluster head
            if 'next_hop' in pck.keys() and pck['dest'] != self.addr and pck['dest'] != self.ch_addr:  # forwards message if destination is not itself
                self.route_and_forward_package(pck)
                return
            if pck['type'] == 'HEART_BEAT':
                self.update_neighbor(pck) 
            if pck['type'] == 'PROBE':  # it waits and sends heart beat message once received probe message
                # yield self.timeout(.5)
                self.send_heart_beat()
            if pck['type'] == 'JOIN_REQUEST':  # it waits and sends join reply message once received join request
                # yield self.timeout(.5)
                current_children = len(self.members_table)
                if current_children < config.MAX_CHILDREN_PER_CLUSTER:
                    net = (self.ch_addr.net_addr if self.ch_addr is not None else self.addr.net_addr)
                    new_addr = wsn.Addr(net, pck['gui'])
                    self.send_join_reply(pck['gui'], new_addr)
                else:
                    self.scene.nodecolor(self.id, 0.5, 0, 1)
                    self.log(f"Cluster head {self.id} full ({current_children}/{config.MAX_CHILDREN_PER_CLUSTER}). Ignored JOIN_REQUEST from node {pck['gui']}.")
            if pck['type'] == 'NETWORK_REQUEST':  # it sends a network reply to requested node
                # yield self.timeout(.5)
                if self.role == Roles.ROOT:
                    new_addr = wsn.Addr(pck['source'].node_addr,254)
                    self.send_network_reply(pck['source'],new_addr)
            if pck['type'] == 'JOIN_ACK':
                self.members_table.append(pck['gui'])
            if pck['type'] == 'NETWORK_UPDATE':
                self.child_networks_table[pck['gui']] = pck['child_networks']
                if self.role != Roles.ROOT:
                    self.send_network_update()
            if pck['type'] == 'SENSOR':
                pass
                # self.log(str(pck['source'])+'--'+str(pck['sensor_value']))

        elif self.role == Roles.REGISTERED:  # if the node is registered
            if pck['type'] == 'HEART_BEAT':
                self.update_neighbor(pck)
            if pck['type'] == 'PROBE':
                # yield self.timeout(.5)
                self.send_heart_beat()
            if pck['type'] == 'JOIN_REQUEST':  # it sends a network request to the root
                self.received_JR_guis.append(pck['gui'])
                # yield self.timeout(.5)
                self.send_network_request()
            if pck['type'] == 'NETWORK_REPLY':  # it becomes cluster head and send join reply to the candidates
                self.set_role(Roles.CLUSTER_HEAD)
                try:
                    write_clusterhead_distances_csv("clusterhead_distances.csv")
                except Exception as e:
                    self.log(f"CH CSV export error: {e}")
                self.scene.nodecolor(self.id, 0, 0, 1)
                self.ch_addr = pck['addr']
                self.send_network_update()
                # yield self.timeout(.5)
                self.send_heart_beat()
                for gui in self.received_JR_guis:
                    # yield self.timeout(random.uniform(.1,.5))
                    self.send_join_reply(gui, wsn.Addr(self.ch_addr.net_addr,gui))

        elif self.role == Roles.UNDISCOVERED:  # if the node is undiscovered
            if pck['type'] == 'HEART_BEAT':  # it kills probe timer, becomes unregistered and sets join request timer once received heart beat
                self.update_neighbor(pck)
                self.kill_timer('TIMER_PROBE')
                self.become_unregistered()

        if self.role == Roles.UNREGISTERED:  # if the node is unregistered
            if pck['type'] == 'HEART_BEAT':
                self.update_neighbor(pck)
            if pck['type'] == 'JOIN_REPLY':  # it becomes registered and sends join ack if the message is sent to itself once received join reply
                if pck['dest_gui'] == self.id:
                    self.addr = pck['addr']
                    self.parent_gui = pck['gui']
                    self.root_addr = pck['root_addr']
                    self.hop_count = pck['hop_count']
                    self.draw_parent()
                    self.kill_timer('TIMER_JOIN_REQUEST')
                    self.send_heart_beat()
                    self.set_timer('TIMER_HEART_BEAT', config.HEARTH_BEAT_TIME_INTERVAL)
                    self.send_join_ack(pck['source'])
                    if self.ch_addr is not None: # it could be a cluster head which lost its parent
                        self.set_role(Roles.CLUSTER_HEAD)
                        self.send_network_update()
                    else:
                        self.set_role(Roles.REGISTERED)
                    # # sensor implementation
                    # timer_duration =  self.id % 20
                    # if timer_duration == 0: timer_duration = 1
                    # self.set_timer('TIMER_SENSOR', timer_duration)

    ###################
    def on_timer_fired(self, name, *args, **kwargs):
        """Executes when a timer fired.

        Args:
            name (string): Name of timer.
            *args (string): Additional args.
            **kwargs (string): Additional key word args.
        Returns:

        """
        if name == 'TIMER_ARRIVAL':  # it wakes up and set timer probe once time arrival timer fired
            self.scene.nodecolor(self.id, 1, 0, 0)  # sets self color to red
            self.wake_up()
            self.set_timer('TIMER_PROBE', 1)

        elif name == 'TIMER_PROBE':
            if self.c_probe < self.th_probe:
                self.send_probe()
                self.c_probe += 1
                self.set_timer('TIMER_PROBE', 1)
            else:
                if self.is_root_eligible:
                    self.set_role(Roles.ROOT)
                    self.scene.nodecolor(self.id, 0, 0, 0)
                    self.addr = wsn.Addr(config.NETWORK_ID, 254)  # (1,254)
                    self.ch_addr = self.addr
                    self.root_addr = self.addr
                    self.hop_count = 0
                    self.set_timer('TIMER_HEART_BEAT', config.HEARTH_BEAT_TIME_INTERVAL)
                else:
                    self.c_probe = 0
                    self.set_timer('TIMER_PROBE', 30)

        elif name == 'TIMER_HEART_BEAT':  # it sends heart beat message once heart beat timer fired
            self.send_heart_beat()
            self.set_timer('TIMER_HEART_BEAT', config.HEARTH_BEAT_TIME_INTERVAL)
            #print(self.id)

        elif name == 'TIMER_JOIN_REQUEST':  # if it has not received heart beat messages before, it sets timer again and wait heart beat messages once join request timer fired.
            if len(self.candidate_parents_table) == 0:
                self.become_unregistered()
            else:  # otherwise it chose one of them and sends join request
                self.select_and_join()

        elif name == 'TIMER_SENSOR':
            self.route_and_forward_package({'dest': self.root_addr, 'type': 'SENSOR', 'source': self.addr, 'sensor_value': random.uniform(10,50)})
            timer_duration =  self.id % 20
            if timer_duration == 0: timer_duration = 1
            self.set_timer('TIMER_SENSOR', timer_duration)
        elif name == 'TIMER_EXPORT_CH_CSV':
            # Only root should drive exports (cheap guard)
            if self.role == Roles.ROOT:
                write_clusterhead_distances_csv("clusterhead_distances.csv")
                # reschedule
                self.set_timer('TIMER_EXPORT_CH_CSV', config.EXPORT_CH_CSV_INTERVAL)
        elif name == 'TIMER_EXPORT_NEIGHBOR_CSV':
            if self.role == Roles.ROOT:
                write_neighbor_distances_csv("neighbor_distances.csv")
                self.set_timer('TIMER_EXPORT_NEIGHBOR_CSV', config.EXPORT_NEIGHBOR_CSV_INTERVAL)
        elif name == 'TIMER_NEIGHBOR_SHARE':
            self.send_neighbor_share()
            self.set_timer('TIMER_NEIGHBOR_SHARE', config.NEIGHBOR_SHARE_INTERVAL)
        elif name == 'TIMER_EXPORT_MULTIHOP_CSV':
            if self.role == Roles.ROOT:
                write_multihop_csv("multihop_neighbors.csv")
                self.set_timer('TIMER_EXPORT_MULTIHOP_CSV', config.EXPORT_NEIGHBOR_CSV_INTERVAL)
    



def write_node_distances_csv(path="node_distances.csv"):
    """Write pairwise node-to-node Euclidean distances as an edge list."""
    ids = sorted(NODE_POS.keys())
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "target_id", "distance"])
        for i, sid in enumerate(ids):
            x1, y1 = NODE_POS[sid]
            for tid in ids[i+1:]:  # i+1 to avoid duplicates and self-pairs
                x2, y2 = NODE_POS[tid]
                dist = math.hypot(x1 - x2, y1 - y2)
                w.writerow([sid, tid, f"{dist:.6f}"])


def write_node_distance_matrix_csv(path="node_distance_matrix.csv"):
    ids = sorted(NODE_POS.keys())
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id"] + ids)
        for sid in ids:
            x1, y1 = NODE_POS[sid]
            row = [sid]
            for tid in ids:
                x2, y2 = NODE_POS[tid]
                dist = math.hypot(x1 - x2, y1 - y2)
                row.append(f"{dist:.6f}")
            w.writerow(row)


def write_clusterhead_distances_csv(path="clusterhead_distances.csv"):
    """Write pairwise distances between current cluster heads."""
    clusterheads = []
    for node in sim.nodes:
        # Only collect nodes that are cluster heads and have recorded positions
        if hasattr(node, "role") and node.role == Roles.CLUSTER_HEAD and node.id in NODE_POS:
            x, y = NODE_POS[node.id]
            clusterheads.append((node.id, x, y))

    if len(clusterheads) < 2:
        # Still write the header so the file exists/is refreshed
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(["clusterhead_1", "clusterhead_2", "distance"])
        return

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["clusterhead_1", "clusterhead_2", "distance"])
        for i, (id1, x1, y1) in enumerate(clusterheads):
            for id2, x2, y2 in clusterheads[i+1:]:
                dist = math.hypot(x1 - x2, y1 - y2)
                w.writerow([id1, id2, f"{dist:.6f}"])

def write_multihop_csv(path="multihop_neighbors.csv"):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "target_gui", "hop", "via_gui", "last_seen"])

        for node in sim.nodes:
            # If we're in 1-hop mode, include direct neighbors as hop=1
            if getattr(config, "NEIGHBOR_SHARE_MAX_HOPS", 1) == 1:
                for tgt in getattr(node, "neighbors_table", {}).keys():
                    w.writerow([node.id, tgt, 1, tgt, ""])

            # Always include multi-hop map (will be empty when K=1)
            for tgt, meta in getattr(node, "multi_neighbors", {}).items():
                w.writerow([node.id, tgt, meta.get("hop"),
                            meta.get("via"), f"{meta.get('last_seen', 0):.3f}"])




def write_neighbor_distances_csv(path="neighbor_distances.csv", dedupe_undirected=True):
    """
    Export neighbor distances per node.
    Each row is (node -> neighbor) with distance from NODE_POS.

    Args:
        path (str): output CSV path
        dedupe_undirected (bool): if True, writes each unordered pair once
                                  (min(node_id,neighbor_id), max(...)).
                                  If False, writes one row per direction.
    """
    # Safety: ensure we can compute distances
    if not globals().get("NODE_POS"):
        raise RuntimeError("NODE_POS is missing; record positions during create_network().")

    # Prepare a set to avoid duplicates if dedupe_undirected=True
    seen_pairs = set()

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "neighbor_id", "distance",
                    "neighbor_role", "neighbor_hop_count", "arrival_time"])

        for node in sim.nodes:
            # Skip nodes without any neighbor info yet
            if not hasattr(node, "neighbors_table"):
                continue

            x1, y1 = NODE_POS.get(node.id, (None, None))
            if x1 is None:
                continue  # no position → cannot compute distance

            # neighbors_table: key = neighbor GUI, value = heartbeat packet dict
            for n_gui, pck in getattr(node, "neighbors_table", {}).items():
                # Optional dedupe (unordered)
                if dedupe_undirected:
                    key = (min(node.id, n_gui), max(node.id, n_gui))
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)

                # Position of neighbor
                x2, y2 = NODE_POS.get(n_gui, (None, None))
                if x2 is None:
                    continue

                # Distance (prefer pck['distance'] if you added it in update_neighbor)
                dist = pck.get("distance")
                if dist is None:
                    dist = math.hypot(x1 - x2, y1 - y2)

                # Extra fields (best-effort; may be missing)
                n_role = getattr(pck.get("role", None), "name", pck.get("role", None))
                hop = pck.get("hop_count", "")
                at  = pck.get("arrival_time", "")

                w.writerow([node.id, n_gui, f"{dist:.6f}", n_role, hop, at])

###########################################################
def create_network(node_class, number_of_nodes=100):
    """Creates given number of nodes at random positions with random arrival times.

    Args:
        node_class (Class): Node class to be created.
        number_of_nodes (int): Number of nodes.
    Returns:

    """
    edge = math.ceil(math.sqrt(number_of_nodes))
    for i in range(number_of_nodes):
        x = i / edge
        y = i % edge
        px = 300 + config.SCALE*x * config.SIM_NODE_PLACING_CELL_SIZE + random.uniform(-1 * config.SIM_NODE_PLACING_CELL_SIZE / 3, config.SIM_NODE_PLACING_CELL_SIZE / 3)
        py = 200 + config.SCALE* y * config.SIM_NODE_PLACING_CELL_SIZE + random.uniform(-1 * config.SIM_NODE_PLACING_CELL_SIZE / 3, config.SIM_NODE_PLACING_CELL_SIZE / 3)
        node = sim.add_node(node_class, (px, py))
        NODE_POS[node.id] = (px, py)   # <— add this line
        node.tx_range = config.NODE_TX_RANGE * config.SCALE
        node.logging = True
        node.arrival = random.uniform(0, config.NODE_ARRIVAL_MAX)
        if node.id == config.ROOT_ID:
            node.arrival = 0.1


sim = wsn.Simulator(
    duration=config.SIM_DURATION,
    timescale=config.SIM_TIME_SCALE,
    visual=config.SIM_VISUALIZATION,
    terrain_size=config.SIM_TERRAIN_SIZE,
    title=config.SIM_TITLE)

# creating random network
create_network(SensorNode, config.SIM_NODE_COUNT)

write_node_distances_csv("node_distances.csv")
write_node_distance_matrix_csv("node_distance_matrix.csv")

# start the simulation
sim.run()
print("Simulation Finished")


# Created 100 nodes at random locations with random arrival times.
# When nodes are created they appear in white
# Activated nodes becomes red
# Discovered nodes will be yellow
# Registered nodes will be green.
# Root node will be black.
# Routers/Cluster Heads should be blue
