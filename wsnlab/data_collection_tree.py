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
LAST_SELF_PROMO_TRY = {}
ORPHAN_SINCE = {}
EVENTS_HEADER_WRITTEN = False

# --- tracking containers ---
ALL_NODES = []              # node objects
CLUSTER_HEADS = []
LAST_CH_DENY_AT = {}
ROOT_SEED_COOLDOWN = {} 
ROLE_COUNTS = Counter()     # live tally per Roles enum

def _addr_str(a): return "" if a is None else str(a)
def _role_name(r): return r.name if hasattr(r, "name") else str(r)


Roles = Enum('Roles', 'UNDISCOVERED UNREGISTERED ROOT REGISTERED CLUSTER_HEAD Router')
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
            self.hop_count = 0
            self.set_role(Roles.ROOT)
            self.scene.nodecolor(self.id, 0, 0, 0)
            print(f"[ROOT] Node {self.id} initialized as ROOT with addr {self.addr}")
        else:
            self.addr = next_short_addr()
            self.hop_count = 99999
            self.set_role(Roles.UNDISCOVERED)
            # print(f"[NODE] Node {self.id} initialized with addr {self.addr}")

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
        self.known_parents = {}      
        self.tried_parents = set()
        self.last_join_target = None
        if getattr(config, "NEIGHBOR_SHARE_MAX_HOPS", 1) > 1:
         self.set_timer('TIMER_NEIGHBOR_SHARE', config.NEIGHBOR_SHARE_INTERVAL)
        self.t_created = getattr(self, "t_created", 0.0)  # set at creation (see step 3)
        self.t_joined  = None
        self.tx_data_sent = 0
        self.tx_data_dropped = 0
        self.rx_data_delivered = 0
        self.energy_tx = 0.0
        self.energy_rx = 0.0
        self.pending_members = set()
        self.tx_level_dbm = getattr(self, "tx_level_dbm", 0)
        self.heard_chs = set()
        self.failed = False
        self.last_parent_hb = None

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
        TX_DBM_CHOICES = [config.TX_DBM_MIN, 0, +6]
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
        if new_role in (Roles.REGISTERED, Roles.CLUSTER_HEAD, Roles.Router):
            if self.id in ORPHAN_SINCE:
                t_orphan = ORPHAN_SINCE.pop(self.id)
                t_now = getattr(self, "now", getattr(sim, "time", 0.0))
                recover_time = t_now - t_orphan
                log_event("RECOVER", self.id,
                          role=new_role.name,
                          recovery_time=f"{recover_time:.6f}")
        if new_role in (Roles.ROOT, Roles.CLUSTER_HEAD):
            if not hasattr(self, "hop_count"):
                self.hop_count = 0 if new_role == Roles.ROOT else 1
            self.send_heart_beat()  # immediate heartbeat (now safe)
            self.set_timer('TIMER_HEART_BEAT', config.HEARTH_BEAT_TIME_INTERVAL)

        if new_role == Roles.REGISTERED and old_role == Roles.CLUSTER_HEAD:
            self.ch_addr = None
            self.cluster_tx_dbm = 0
            self.tx_range = _range_for_dbm(0) * config.SCALE
            self.scene.nodecolor(self.id, 0, 1, 0)

        if new_role == Roles.CLUSTER_HEAD:
            log_event("BECOME_CH", self.id,
                    hop_count=getattr(self, "hop_count", ""),
                    tx_dbm=getattr(self, "cluster_tx_dbm", ""))    

        if recolor:
            if new_role == Roles.UNDISCOVERED:
                self.scene.nodecolor(self.id, 1, 1, 1)
            elif new_role == Roles.UNREGISTERED:
                self.scene.nodecolor(self.id, 1, 1, 0)
            elif new_role == Roles.REGISTERED:
                self.send_heart_beat()
                self.set_timer('TIMER_HEART_BEAT', config.HEARTH_BEAT_TIME_INTERVAL)
                self.scene.nodecolor(self.id, 0, 1, 0)
            elif new_role == Roles.CLUSTER_HEAD:
                # Only set a default if not already set (e.g., from promotion logic)
                if not hasattr(self, "cluster_tx_dbm"):
                    self.cluster_tx_dbm = getattr(config, "CH_PROMOTION_DBM", +6)

                self.tx_level_dbm = self.cluster_tx_dbm
                self.set_timer("TIMER_CH_ROTATE", random.uniform(120, 200))
                self.tx_range = _range_for_dbm(self.cluster_tx_dbm) * config.SCALE

                self.scene.nodecolor(self.id, 0, 0, 1)
                self.draw_tx_range()
            elif new_role == Roles.ROOT:
                self.cluster_tx_dbm = getattr(config, "ROOT_TX_DBM", +12)
                self.tx_level_dbm = self.cluster_tx_dbm
                self.tx_range = _range_for_dbm(self.cluster_tx_dbm) * config.SCALE

                self.scene.nodecolor(self.id, 0, 0, 0)
                self.set_timer('TIMER_ROOT_SEED', 8)  
                self._seed_fail_count = 0 
                self.set_timer('TIMER_CH_SEED', 25)
                self.set_timer('TIMER_EXPORT_CH_CSV', config.EXPORT_CH_CSV_INTERVAL)
                self.set_timer('TIMER_EXPORT_NEIGHBOR_CSV', config.EXPORT_NEIGHBOR_CSV_INTERVAL)
                self.set_timer('TIMER_EXPORT_MULTIHOP_CSV', config.EXPORT_NEIGHBOR_CSV_INTERVAL)
                self.set_timer('TIMER_CLUSTER_OPT', getattr(config, "CLUSTER_OPT_INTERVAL", 80))
            elif new_role == Roles.Router:
                self.tx_level_dbm = 6
                self.tx_range = _range_for_dbm(self.tx_level_dbm) * config.SCALE
                self.scene.nodecolor(self.id, 1, 0, 1)   

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
        self.received_JR_guis = []
        self.send_probe()
        self.set_timer('TIMER_JOIN_REQUEST', 20)
    
    def _has_send_addr(self, gui):
        rec = self.neighbors_table.get(gui, {})
        return bool(rec.get("addr") or rec.get("ch_addr") or rec.get("send_addr") or rec.get("source"))

    def _can_forward_to(self, dst_gui):
        """
        Only consider a destination reachable if we either:
        - can send directly (1 hop), or
        - have a multi-hop mesh path (<= LOCAL_MESH_MAX_HOPS).
        We explicitly *disallow* pure 'tree' fallback for arbitrary peers,
        because that just climbs toward the root and tends to be dropped there.
        """
        if dst_gui in self.neighbors_table:
            nh_rec = self.neighbors_table.get(dst_gui, {})
            return bool(nh_rec.get("addr") or nh_rec.get("ch_addr") or nh_rec.get("send_addr") or nh_rec.get("source"))

        meta = self.multi_neighbors.get(dst_gui)
        if meta and meta.get("hop", 99999) <= config.LOCAL_MESH_MAX_HOPS():
            via = meta.get("via")
            if via in self.neighbors_table:
                nh_rec = self.neighbors_table.get(via, {})
                return bool(nh_rec.get("addr") or nh_rec.get("ch_addr") or nh_rec.get("send_addr") or nh_rec.get("source"))

        return False   
    def _send_with_loss(self, pck):
        """Apply Bernoulli drop to DATA packets; forward others unchanged."""
        if pck.get("type") == "DATA":
            self.tx_data_sent += 1
            if random.random() < getattr(config, "PACKET_LOSS_PROB", 0.0):
                self.tx_data_dropped += 1
                return  # dropped
        self.send(pck)

    ###################
    def update_neighbor(self, pck):
        pck['arrival_time'] = self.now
        if pck['gui'] in NODE_POS and self.id in NODE_POS:
            x1, y1 = NODE_POS[self.id]
            x2, y2 = NODE_POS[pck['gui']]
            pck['distance'] = math.hypot(x1 - x2, y1 - y2)
        send_addr = pck.get('addr') or pck.get('ch_addr') or pck.get('source')
        if send_addr is not None:
            pck.setdefault('addr', send_addr)
            pck.setdefault('ch_addr', send_addr)
            pck['send_addr'] = send_addr    
            self.neighbors_table[pck['gui']] = pck
        if pck['gui'] == self.parent_gui:
            self.last_parent_hb = self.now   

        if pck['gui'] not in self.child_networks_table.keys() or pck['gui'] not in self.members_table:
            if pck['gui'] not in self.candidate_parents_table:
                self.candidate_parents_table.append(pck['gui'])
        r = pck.get("role", None)
        if r == Roles.CLUSTER_HEAD or getattr(r, "name", None) == "CLUSTER_HEAD":
            self.heard_chs.add(pck["gui"])        

    ###################
    def select_and_join(self):
        eligible = [g for g in self.candidate_parents_table if g not in self.tried_parents]
        if not eligible:
            self.tried_parents.clear()
            eligible = self.candidate_parents_table[:]
            if not eligible:
                return

        min_hop = min(self.neighbors_table[g]['hop_count'] for g in eligible)
        tier = [g for g in eligible if self.neighbors_table[g]['hop_count'] == min_hop]
        min_hop_gui = random.choice(tier)

        selected_addr = self.neighbors_table[min_hop_gui]['source']
        self.last_join_target = min_hop_gui
        self.tried_parents.add(min_hop_gui)

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
           'hop_count': self.hop_count+1, 'tx_dbm': getattr(self, "cluster_tx_dbm", 0)})

    ###################
    def send_join_ack(self, dest):
        """Sending join acknowledgement message to given destination address.

        Args:
            dest (Addr): Address of destination node
        Returns:

        """
        if dest is None:
            # Fallback: just broadcast the ACK instead of crashing
            dest = wsn.BROADCAST_ADDR

        self.send({
            'dest': dest,
            'type': 'JOIN_ACK',
            'source': self.addr,
            'gui': self.id,
        })

    def send_data(self, dst_gui, payload=None):
        if dst_gui is None or dst_gui == self.id:
            return
        pck = {
            "type": "DATA",
            "source_gui": self.id,
            "dst_gui": dst_gui,
            "payload": payload if payload is not None else {},
            "ts_created": self.now,
            "ttl": 48,
        }
        self.route_and_forward_package(pck)    
    ###################
    def _now(self):  # tiny helper
        return getattr(self, "now", 0)
    
    def build_neighbor_summary(self):
        """
        Our view of neighbors up to K hops:
        - 1-hop neighbors from neighbors_table as hop=1
        - multi-hop entries from self.multi_neighbors as their stored hop
        We include our own parent_gui so receivers can route to us via our parent.
        """
        K = getattr(config, "NEIGHBOR_SHARE_MAX_HOPS", 1)
        summary = {}

        for n_gui in self.neighbors_table.keys():
            summary[n_gui] = 1
        for t_gui, meta in self.multi_neighbors.items():
            h = meta.get("hop", 99999)
            if h <= K:
                if t_gui not in summary or h < summary[t_gui]:
                    summary[t_gui] = h
        summary.pop(self.id, None)

        items = []
        for gid, hop in summary.items():
            items.append({"gui": gid, "hop": hop})
        items.append({"gui": self.id, "hop": 0, "parent_gui": getattr(self, "parent_gui", None)})

        return items
    
    def send_neighbor_share(self):
        """Broadcast our neighbor summary periodically."""
        self._share_seq += 1
        payload = {
            "dest": wsn.BROADCAST_ADDR,
            "type": "NEIGHBOR_SHARE",
            "from_gui": self.id,
            "seq": self._share_seq,
            "items": self.build_neighbor_summary(),
            "sender_addr": (self.ch_addr if self.ch_addr is not None else self.addr),
        }
        self.send(payload)

    def evaluate_router_role(self):
        """
        Decide whether this node should be a Router based on how many
        distinct cluster heads it can hear.
        """
        min_ch = getattr(config, "ROUTER_MIN_CLUSTER_HEADS", 2)

        # rebuild heard CH list
        self.heard_chs = {
            g for g, rec in self.neighbors_table.items()
            if rec.get("role") == Roles.CLUSTER_HEAD
            or getattr(rec.get("role"), "name", None) == "CLUSTER_HEAD"
        }

        count = len(self.heard_chs)

        # promote / demote
        if self.role == Roles.REGISTERED and count >= min_ch:
            self.set_role(Roles.Router)

        elif self.role == Roles.Router and count < min_ch:
            self.set_role(Roles.REGISTERED)    

   

    def try_promote_self(self):
        """Periodic self-check: if I'm far enough from any CH, ask the root to promote me."""
        # Only active for registered nodes
        if getattr(self, "role", None) != Roles.REGISTERED:
            return

        now = getattr(self, "now", 0.0)
        last_try = LAST_SELF_PROMO_TRY.get(self.id, -1e9)
        cooldown = max(8.0, getattr(config, "CH_DENY_COOLDOWN", 30) / 3)
        if now - last_try < cooldown:
            return
        LAST_SELF_PROMO_TRY[self.id] = now

        # --- distance geometry ---
        d_nearest = _nearest_ch_distance(self.id)
        nearest_rng = 0.0
        nearest_id = None
        if d_nearest is not None:
            cx, cy = NODE_POS[self.id]
            for n in sim.nodes:
                if getattr(n, "role", None) != Roles.CLUSTER_HEAD or n.id not in NODE_POS or n.id == self.id:
                    continue
                x, y = NODE_POS[n.id]
                d = math.hypot(cx - x, cy - y)
                if abs(d - d_nearest) < 1e-6:
                    nearest_rng = getattr(n, "tx_range", 0.0)
                    nearest_id = n.id
                    break

        cand_range = _range_for_dbm(config.CLUSTER_TX_DBM) * config.SCALE
        mode = getattr(config, "CH_SEPARATION_MODE", "candidate")

        if mode == "max":
            sep_radius = max(cand_range, nearest_rng)
        else:
            sep_radius = cand_range

        min_required = getattr(config, "CH_MIN_SEPARATION_FACTOR", 1.0) * sep_radius
        allow = (d_nearest is None) or (d_nearest >= min_required)

        if getattr(config, "CH_DEBUG_LOG", False):
            self.log(
                f"[CH-SELF] cand {self.id} nearestCH={-1.0 if d_nearest is None else d_nearest:.1f} "
                f"cand_rng={cand_range:.1f} near_rng={nearest_rng:.1f} mode={mode} "
                f"min_required={min_required:.1f} -> {'REQ' if allow else 'SKIP'}"
            )

        if allow:
            # Ask the root to promote me. Root already handles NETWORK_REQUEST → NETWORK_REPLY.
            self.send_network_request()  

    def merge_neighbor_share(self, pck):
        """
        Merge a received NEIGHBOR_SHARE into our multi-hop table.
        The sender’s hop value is its distance to each target; for us it's +1.
        We track the best (smallest-hop) path and 'via' (the sender), and
        remember parent(DEST) hints for the routing rule:
        If Parent(DEST) ∈ NeighborTable → NextHop = Parent(DEST)
        """
        sender = pck.get("from_gui")
        if sender is None:
            return

        seq = pck.get("seq")
        last = self._seen_share.get(sender, -1)
        if seq is not None and seq <= last:
            return
        self._seen_share[sender] = seq

        saddr = pck.get("sender_addr") or pck.get("source")
        if saddr is not None and sender not in self.neighbors_table:
            self.neighbors_table[sender] = {
                "gui": sender,
                "addr": saddr, "ch_addr": saddr, "send_addr": saddr, "source": saddr,
                "role": None, "hop_count": 1, "arrival_time": self.now,
            }

        items = pck.get("items", [])
        now = self._now()
        K = getattr(config, "NEIGHBOR_SHARE_MAX_HOPS", 1)

        for it in items:
            tgt = it.get("gui")
            h_sender = it.get("hop", 99999)
            if tgt is None or tgt == self.id:
                continue

            # keep this: only trust self-reported parent (prevents bogus hints)
            p_dest = it.get("parent_gui")
            if p_dest is not None and tgt == sender:
                if not hasattr(self, "known_parents"):
                    self.known_parents = {}
                self.known_parents[tgt] = p_dest

            h_here = h_sender + 1
            if h_here > K:
                continue

            best = self.multi_neighbors.get(tgt)
            if (best is None) or (h_here < best.get("hop", 99999)) or \
            (h_here == best.get("hop", 99999) and best.get("via", 1e9) > sender):
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

    def choose_next_hop(self, dst_gui, prev_gui=None):
            """
            Implements routing rules:
            1) If traffic is on the backbone (ROOT/CH/Router ↔ ROOT/CH/Router),
            prefer Routers as next-hop.
            2) If DEST is in Neighbor Table → Next Hop = DEST (direct),
            except we avoid direct CH↔CH / CH↔ROOT when we're a CH.
            3) Else if Parent(DEST) in Neighbor Table → Next Hop = Parent(DEST) (mesh).
            4) Else if multi-hop mesh known → Next Hop = best 'via' (mesh).
            5) Else if we have a parent → Tree fallback.
            6) Else unreachable.
            """
            my_role = getattr(self, "role", None)
            self_is_core = my_role in (Roles.ROOT, Roles.CLUSTER_HEAD)
            dst_is_core = _is_core_node(dst_gui) or (dst_gui == config.ROOT_ID)
            if (self_is_core or dst_is_core) and self.neighbors_table:
                router_candidates = []
                for nb_gui, rec in self.neighbors_table.items():
                    if nb_gui == prev_gui:
                        continue
                    r = rec.get("role")
                    if r == Roles.Router or getattr(r, "name", None) == "Router":
                        router_candidates.append(nb_gui)

                if router_candidates:
                    nh_gui = min(router_candidates)
                    return (nh_gui, "mesh")
            if dst_gui in self.neighbors_table and dst_gui != prev_gui:
                dst_rec = self.neighbors_table[dst_gui]
                dst_role = dst_rec.get("role")
                if not (self_is_core and (dst_role == Roles.CLUSTER_HEAD or dst_gui == config.ROOT_ID)):
                    mode = "direct"
                    if dst_gui == config.ROOT_ID and self.parent_gui != config.ROOT_ID:
                        mode = "mesh"  # relabel for reporting
                    return (dst_gui, mode)
            p_dest = self.known_parents.get(dst_gui)
            if p_dest is not None and p_dest in self.neighbors_table and p_dest != prev_gui:
                return (p_dest, "mesh")
            Kmesh = config.LOCAL_MESH_MAX_HOPS()
            meta = self.multi_neighbors.get(dst_gui)
            if meta and meta.get("hop", 99999) <= Kmesh:
                via = meta.get("via")
                if via in self.neighbors_table and via != prev_gui:
                    return (via, "mesh")
            if self.parent_gui is not None and self.parent_gui != prev_gui:
                return (self.parent_gui, "tree")

            return (None, "unreachable")
                        
    
    def route_and_forward_package(self, pck):
        trace = pck.setdefault("trace", [])
        if not trace or trace[-1] != self.id:
            trace.append(self.id)
        if len(pck.get("trace", [])) > 24:   # hard cap; adjust as you like
            if getattr(config, "DEBUG_ROUTING", False):
                self.log(f"[DROP] path too long; dropping. trace={pck['trace']}")
            return    

        if len(trace) >= 4 and trace[-1] == trace[-3] and trace[-2] == trace[-4]:
            if getattr(config, "DEBUG_ROUTING", False):
                self.log(f"[DROP] ABAB oscillation {trace[-4:]}")
            return

        if len(trace) >= 2:
            new_edge = (min(trace[-2], trace[-1]), max(trace[-2], trace[-1]))
            # build all prior edges from trace (except the last one we just added)
            prior_edges = set(
                (min(trace[i], trace[i+1]), max(trace[i], trace[i+1]))
                for i in range(len(trace)-2)
            )
            if new_edge in prior_edges:
                if getattr(config, "DEBUG_ROUTING", False):
                    self.log(f"[DROP] repeated edge via trace-only guard {new_edge}; trace len={len(trace)}")
                return

        if self.id in trace[:-1]:
            if getattr(config, "DEBUG_ROUTING", False):
                self.log(f"[DROP] cycle revisit detected path={trace}")
            return

        if getattr(config, "ENABLE_PACKET_TRACING", False):
            tr = pck.setdefault("trace", [])
            if not tr or tr[-1] != self.id:
                tr.append(self.id)

        dst_gui = pck.get("dst_gui")
        prev_gui = pck.get("last_hop_gui")
        if prev_gui is not None:
            edge = (min(prev_gui, self.id), max(prev_gui, self.id))
            edge_hist = pck.setdefault("edge_hist", [])
            if edge in edge_hist:
                if getattr(config, "DEBUG_ROUTING", False):
                    self.log(f"[DROP] edge repeat guard {edge} trace={pck.get('trace', [])}")
                return
            edge_hist.append(edge)

        if dst_gui is not None:
            nh_gui, mode = self.choose_next_hop(dst_gui, prev_gui)

            if nh_gui is not None and nh_gui == prev_gui:
                if getattr(config, "DEBUG_ROUTING", False):
                    self.log(f"[DROP] immediate bounce prevented (prev={prev_gui}) dst={dst_gui}")
                return

            if nh_gui is not None and prev_gui is not None and nh_gui == prev_gui:
                if self.parent_gui in self.neighbors_table and self.parent_gui != prev_gui:
                    nh_gui = self.parent_gui
                    mode = "tree"
                else:
                    if getattr(config, "DEBUG_ROUTING", False):
                        self.log(f"[DROP] Bounce detected (prev={prev_gui}) and no fallback at {self.id} for dst {dst_gui}")
                    return

            pck["route_mode"] = mode

            if nh_gui is None:
                if self.parent_gui in self.neighbors_table:
                    nh_gui = self.parent_gui
                    pck["route_mode"] = "tree"
                else:
                    self.log(f"[DROP] No route from {self.id} to {dst_gui}")
                    return

            nh_rec = self.neighbors_table.get(nh_gui, {})
            nh_addr = (
                nh_rec.get("addr")
                or nh_rec.get("ch_addr")
                or nh_rec.get("send_addr")
                or nh_rec.get("source")
            )

            if nh_addr is None and self.parent_gui in self.neighbors_table and nh_gui != self.parent_gui:
                parent_rec = self.neighbors_table[self.parent_gui]
                nh_addr = (
                    parent_rec.get("addr")
                    or parent_rec.get("ch_addr")
                    or parent_rec.get("send_addr")
                    or parent_rec.get("source")
                )
                pck["route_mode"] = "tree"

            if nh_addr is None:
                self.log(f"[DROP] No usable next-hop address: {nh_gui} from {self.id}")
                return

            pck.setdefault("route_modes", []).append(pck["route_mode"])

            pck.setdefault("ts_created", self.now)

            pck["next_hop"] = nh_addr
            pck["last_hop_gui"] = self.id

            hop_energy = config.E_TX_BASE
            if self.id in NODE_POS:
                sx, sy = NODE_POS[self.id]
                if nh_gui in NODE_POS:
                    dx, dy = NODE_POS[nh_gui]
                    d = math.hypot(sx - dx, sy - dy)
                    hop_energy += config.E_TX_DIST_COEFF * (d ** 2)

            self.energy_tx = getattr(self, "energy_tx", 0.0) + hop_energy
            pck["energy_cost"] = pck.get("energy_cost", 0.0) + hop_energy
            self._send_with_loss(pck)
            return

        if self.role != Roles.ROOT and self.parent_gui in self.neighbors_table:
            parent_rec = self.neighbors_table[self.parent_gui]
            pck['next_hop'] = parent_rec.get('ch_addr') or parent_rec.get('addr')

        if self.ch_addr is not None and 'dest' in pck:
            if pck['dest'].net_addr == self.ch_addr.net_addr:
                pck['next_hop'] = pck['dest']
            else:
                for child_gui, child_networks in self.child_networks_table.items():
                    if pck['dest'].net_addr in child_networks and child_gui in self.neighbors_table:
                        child_rec = self.neighbors_table[child_gui]
                        pck['next_hop'] = child_rec.get('addr') or child_rec.get('ch_addr')
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
        """Send our child-network list to our parent (if we still have one)."""
        if self.parent_gui is None:
            return
        parent_rec = self.neighbors_table.get(self.parent_gui)
        if not parent_rec:
            return  # parent not currently reachable; try again later

        dest = (parent_rec.get('ch_addr') or
                parent_rec.get('addr') or
                parent_rec.get('send_addr') or
                parent_rec.get('source'))
        if not dest:
            return

        child_networks = [self.ch_addr.net_addr] if self.ch_addr else []
        for networks in self.child_networks_table.values():
            child_networks.extend(networks)

        self.send({
            'dest': dest,
            'type': 'NETWORK_UPDATE',
            'source': self.addr,
            'gui': self.id,
            'child_networks': child_networks
        })

    def evaluate_cluster_head_rotation(self):
        """
        Decide whether to transfer CH role to another node.
        We pick a REGISTERED node that:
        - is within our cluster (1 hop)
        - has the best separation metric (farthest from other CHs)
        """
        # Collect eligible candidates
        candidates = []
        for gui, rec in self.neighbors_table.items():
            node_role = rec.get("role")
            if node_role == Roles.REGISTERED:
                candidates.append(gui)

        if not candidates:
            return  # no one to handover to

        # Pick best candidate based on distance to nearest CH
        best_gui = None
        best_dist = -1
        
        for gui in candidates:
            d = _nearest_ch_distance(gui)
            if d is None:
                d = 9999
            if d > best_dist:
                best_dist = d
                best_gui = gui

        if best_gui is None:
            return

        # Send handover request
        self.send({
            'dest': self.neighbors_table[best_gui].get("addr"),
            'type': 'CH_HANDOVER_REQUEST',
            'source_gui': self.id,
            'target_gui': best_gui
        })    

    ###################
    def on_receive(self, pck):
        """Executes when a package received.

        Args:
            pck (Dict): received package
        Returns:

        """
        self.energy_rx = getattr(self, "energy_rx", 0.0) + config.E_RX_BASE
        pck.setdefault("ttl", 32)
        pck.setdefault("visited", [])
        if self.id in pck["visited"] or pck["ttl"] <= 0:
            return  # drop
        
        pck["visited"].append(self.id)
        pck["ttl"] -= 1
        if pck.get("type") == "NEIGHBOR_SHARE":
                self.merge_neighbor_share(pck)
                return
        if pck.get("type") == "CH_HANDOVER_REQUEST":
            target = pck.get("target_gui")
            if target == self.id:
                # Accept handover
                self.send({
                    'dest': self.neighbors_table[pck['source_gui']]['addr'],
                    'type': 'CH_HANDOVER_ACCEPT',
                    'source_gui': self.id
                })
            return

        if pck.get("type") == "CH_HANDOVER_ACCEPT":
            new_ch_gui = pck.get("source_gui")

            if self.role == Roles.CLUSTER_HEAD and new_ch_gui is not None:
                old_ch_addr = getattr(self, "ch_addr", None)
                old_tx_dbm  = getattr(self, "cluster_tx_dbm", getattr(config, "CH_PROMOTION_DBM", 6))

                new_ch = None
                for n in sim.nodes:
                    if n.id == new_ch_gui:
                        new_ch = n
                        break

                if new_ch is not None:
                    new_ch.ch_addr = old_ch_addr
                    new_ch.root_addr = self.root_addr
                    new_ch.cluster_tx_dbm = old_tx_dbm
                    new_ch.tx_level_dbm = old_tx_dbm
                    new_ch.tx_range = _range_for_dbm(old_tx_dbm) * config.SCALE
                    new_ch.set_role(Roles.CLUSTER_HEAD)
                    self.ch_addr = None
                    self.set_role(Roles.REGISTERED)
            return
        if pck.get("type") == "DATA":
            if pck.get("dst_gui") == self.id:
                self.rx_data_delivered += 1
                trace = pck.setdefault("trace", [])

                if not trace or trace[-1] != self.id:
                    trace.append(self.id)

                tag = pck.get("payload", {}).get("tag")
                if tag == "TRACE_TO_ROOT":
                    print(f"[ROUTE] {pck.get('source_gui')} -> {self.id} : {'->'.join(map(str, trace))}")

                modes = pck.get("route_modes", [])
                if "tree" in modes:
                    overall = "tree"
                elif "mesh" in modes:
                    overall = "mesh"
                else:
                    overall = "direct"               
                last_hop_mode = modes[-1] if modes else ""
                safe_trace = trace if len(trace) <= 60 else (trace[:30] + ["…"] + trace[-29:])
                path_str = "->".join(map(str, safe_trace))

                ts_created = pck.get("ts_created")
                total_energy = pck.get("energy_cost", 0.0)

                if ts_created is not None:
                    delay = self.now - ts_created
                    try:
                        with open("packet_delays.csv", "a", newline="") as f:
                            csv.writer(f).writerow([
                                pck.get("source_gui"),
                                self.id,
                                f"{delay:.6f}",
                                overall,
                                last_hop_mode,
                                path_str
                            ])
                    except Exception:
                        pass

                # log per-packet energy consumption
                try:
                    with open("packet_energy.csv", "a", newline="") as f:
                        csv.writer(f).writerow([
                            pck.get("source_gui"),
                            self.id,
                            f"{total_energy:.6f}",
                            overall,
                            last_hop_mode,
                            path_str
                        ])
                except Exception:
                    pass

                return
            else:
                # set previous hop for the next router
                pck["last_hop_gui"] = self.id
                self.route_and_forward_package(pck)
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
            if pck['type'] == 'JOIN_REQUEST':
                current_children = len(self.members_table) + len(self.pending_members)

                if current_children >= config.MAX_CHILDREN_PER_CLUSTER:
                    self.scene.nodecolor(self.id, 0.5, 0, 1)
                    self.log(f"Cluster head {self.id} full ({current_children}/{config.MAX_CHILDREN_PER_CLUSTER}). "
                            f"Ignored JOIN_REQUEST from node {pck['gui']}.")
                    return

                # Reserve a slot immediately to avoid races
                self.pending_members.add(pck['gui'])

                net = (self.ch_addr.net_addr if self.ch_addr is not None else self.addr.net_addr)
                new_addr = wsn.Addr(net, pck['gui'])
                self.send_join_reply(pck['gui'], new_addr)
            if pck['type'] == 'NETWORK_REQUEST':  # it sends a network reply to requested node
                # yield self.timeout(.5)
                if self.role == Roles.ROOT:
                    new_addr = wsn.Addr(pck['source'].node_addr,254)
                    self.send_network_reply(pck['source'],new_addr)
            if pck['type'] == 'JOIN_ACK':
                gui = pck['gui']
                # finalize reservation
                if gui in self.pending_members:
                    self.pending_members.remove(gui)
                    if gui not in self.members_table:
                        self.members_table.append(gui)
                else:
                    if gui not in self.members_table:
                        if len(self.members_table) < config.MAX_CHILDREN_PER_CLUSTER:
                            self.members_table.append(gui)
                        else:
                            self.log(f"Cluster head {self.id} got late ACK from {gui} but capacity is full; ignoring.")
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
            if pck['type'] == 'NETWORK_REPLY':
                now = getattr(self, "now", 0.0)
                last_deny = LAST_CH_DENY_AT.get(self.id, -1e9)
                if now - last_deny < getattr(config, "CH_DENY_COOLDOWN", 30):
                    if getattr(config, "CH_DEBUG_LOG", False):
                        self.log(f"[CH-GATE] cooldown active ({now - last_deny:.1f}s since deny); skip promotion attempt")
                    return

                prom_dbm = getattr(config, "CH_PROMOTION_DBM", +6) 
                cand_range = _range_for_dbm(config.CLUSTER_TX_DBM) * config.SCALE
                d_nearest = _nearest_ch_distance(self.id)
                nearest_rng = 0.0
                nearest_id = None
                if d_nearest is not None:
                    cx, cy = NODE_POS[self.id]
                    for n in sim.nodes:
                        if getattr(n, "role", None) != Roles.CLUSTER_HEAD or n.id not in NODE_POS or n.id == self.id:
                            continue
                        x, y = NODE_POS[n.id]
                        d = math.hypot(cx - x, cy - y)
                        if abs(d - d_nearest) < 1e-6:
                            nearest_rng = getattr(n, "tx_range", 0.0)
                            nearest_id = n.id
                            break

                if getattr(config, "CH_SEPARATION_MODE", "candidate") == "max":
                    sep_radius = max(cand_range, nearest_rng)
                else:
                    sep_radius = cand_range

                min_required = config.CH_MIN_SEPARATION_FACTOR * sep_radius
                allow_ch = (d_nearest is None) or (d_nearest >= min_required)

                if getattr(config, "CH_DEBUG_LOG", False):
                    self.log(f"[CH-GATE] cand {self.id} nearestCH={d_nearest if d_nearest is not None else -1:.1f} "
                            f"(id={nearest_id}) cand_rng={cand_range:.1f} near_rng={nearest_rng:.1f} "
                            f"mode={getattr(config,'CH_SEPARATION_MODE','candidate')} "
                            f"min_required={min_required:.1f} -> {'ALLOW' if allow_ch else 'DENY'}")

                if not allow_ch:
                    LAST_CH_DENY_AT[self.id] = now
                    if getattr(config, "CH_DEBUG_LOG", False):
                        self.log(f"[CH-GATE] {self.id} defers CH role due to overlap; remaining REGISTERED.")
                    return

                self.cluster_tx_dbm = prom_dbm
                self.tx_level_dbm = prom_dbm
                self.tx_range = _range_for_dbm(prom_dbm) * config.SCALE

                self.set_role(Roles.CLUSTER_HEAD)
                try:
                    write_clusterhead_distances_csv("clusterhead_distances.csv")
                except Exception as e:
                    self.log(f"CH CSV export error: {e}")

                self.scene.nodecolor(self.id, 0, 0, 1)
                self.ch_addr = pck['addr']
                self.send_network_update()
                self.send_heart_beat()
                for gui in self.received_JR_guis:
                    self.send_join_reply(gui, wsn.Addr(self.ch_addr.net_addr, gui))

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
                    timer_duration = self.id % 20 or 1
                    self.set_timer('TIMER_SENSOR', timer_duration)
                    self.send_join_ack(pck['source'])
                    if self.ch_addr is not None: # it could be a cluster head which lost its parent
                        self.set_role(Roles.CLUSTER_HEAD)
                        self.send_network_update()
                    else:
                        self.set_role(Roles.REGISTERED)
                        self.set_timer('TIMER_CH_SELF_CHECK', random.uniform(6, 12))
                        self.set_timer('TIMER_TRACE_TO_ROOT', random.uniform(12, 20))   
                        self.set_timer('TIMER_ROUTER_EVAL', random.uniform(20, 40)) 
                    if "tx_dbm" in pck:
                        self.tx_level_dbm = pck["tx_dbm"]
                    else:
                        self.tx_level_dbm = getattr(self, "tx_level_dbm", 0)

                    self.tx_range = _range_for_dbm(self.tx_level_dbm) * config.SCALE
                    self.t_joined = self.now
                    join_time = self.t_joined - (self.t_created or 0.0)
                    try:
                        with open("join_times.csv", "a", newline="") as f:
                            csv.writer(f).writerow([self.id, f"{join_time:.6f}"])
                    except Exception:
                        pass
                    log_event("JOINED", self.id,
                    parent_gui=self.parent_gui,
                    role=str(self.role),
                    join_time=f"{join_time:.6f}")
                    self.last_parent_hb = self.now
                    self.set_timer("TIMER_PARENT_CHECK", getattr(config, "PARENT_CHECK_INTERVAL", 20))    
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
                    self.set_timer('TIMER_EXPORT_METRICS', config.METRICS_EXPORT_INTERVAL)
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
        
        elif name == "TIMER_PARENT_CHECK":
            # if we have a parent, ensure it is alive via recent heartbeat
            if self.parent_gui is not None:
                timeout = getattr(config, "PARENT_DEAD_TIMEOUT", 60.0)
                last = getattr(self, "last_parent_hb", None)
                if last is None or (self.now - last) > timeout:
                    # parent considered dead -> we become orphan and rejoin
                    if self.id not in ORPHAN_SINCE:
                        ORPHAN_SINCE[self.id] = self.now
                        log_event("ORPHAN", self.id,
                                  parent_gui=self.parent_gui,
                                  role=str(getattr(self, "role", None)))
                    self.become_unregistered()
                else:
                    self.set_timer("TIMER_PARENT_CHECK",
                                   getattr(config, "PARENT_CHECK_INTERVAL", 20))    

        elif name == 'TIMER_JOIN_REQUEST':
            if len(self.candidate_parents_table) == 0:
                self.become_unregistered()
            else:  
                self.select_and_join()
        
        elif name == 'TIMER_EXPORT_METRICS':
            if self.role == Roles.ROOT:
                export_metrics_summary()
                self.set_timer('TIMER_EXPORT_METRICS', config.METRICS_EXPORT_INTERVAL)

        elif name == 'TIMER_ROOT_SEED':
            root_seed_new_ch()
            self.set_timer('TIMER_ROOT_SEED', 8) 

        elif name == 'TIMER_CLUSTER_OPT':
            if self.role == Roles.ROOT:
                optimize_clusters()
                self.set_timer('TIMER_CLUSTER_OPT',
                getattr(config, "CLUSTER_OPT_INTERVAL", 80))    

        elif name == "TIMER_CH_ROTATE":
            if self.role == Roles.CLUSTER_HEAD:
                self.evaluate_cluster_head_rotation()
            self.set_timer("TIMER_CH_ROTATE", random.uniform(120, 200))          

        elif name == 'TIMER_CH_SELF_CHECK':
            if self.role == Roles.REGISTERED:
                self.try_promote_self()
                self.set_timer('TIMER_CH_SELF_CHECK', random.uniform(12, 18))   

        elif name == "TIMER_FAIL_NODE":
            self.fail_node()

        elif name == "TIMER_RECOVER_NODE":
            self.recover_node()        

        elif name == 'TIMER_CH_SEED':
            if self.role == Roles.ROOT:
                root_seed_new_ch()
                self.set_timer('TIMER_CH_SEED', 35)

        elif name == 'TIMER_ROUTER_EVAL':
            self.evaluate_router_role()
            self.set_timer('TIMER_ROUTER_EVAL',
                           getattr(config, "ROUTER_EVAL_INTERVAL", 60))                         

        elif name == 'TIMER_SENSOR':
            if not self.neighbors_table and not self.multi_neighbors:
                period = getattr(config, "BASE_SENSOR_PERIOD", 40) + \
                random.uniform(0, getattr(config, "SENSOR_JITTER", 10))
                self.set_timer('TIMER_SENSOR', period)
                return

            one_hop_targets = [g for g in self.neighbors_table.keys() if g != self.id]
            mesh_targets = [g for g, meta in self.multi_neighbors.items()
                            if g != self.id and meta.get("hop", 99) <= config.LOCAL_MESH_MAX_HOPS()]

            candidate_set = []
            seen = set()
            for g in mesh_targets + one_hop_targets:
                if g not in seen and self._can_forward_to(g):
                    candidate_set.append(g)
                    seen.add(g)

            if candidate_set:
                dst_gui = random.choice(candidate_set)
                self.send_data(dst_gui, {"sensor_value": random.uniform(10, 50)})

            self.set_timer('TIMER_SENSOR', self.id % 20 or 1)

        elif name == 'TIMER_TRACE_TO_ROOT':
            if getattr(self, "parent_gui", None) is not None or self.role in (Roles.CLUSTER_HEAD,):
                pck = {
                    "type": "DATA",
                    "source_gui": self.id,
                    "dst_gui": config.ROOT_ID,
                    "payload": {"tag": "TRACE_TO_ROOT"},
                    "ts_created": self.now,
                    "ttl": 64,
                    "trace": [self.id],  # seed path with self
                }
                self.route_and_forward_package(pck)

    
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
        
    def fail_node(self):
        """Simulate node failure: stop participating, kill timers."""
        log_event("NODE_FAIL", self.id, role=str(getattr(self, "role", None)))
        self.failed = True
        self.kill_all_timers()
        self.scene.nodecolor(self.id, 0.3, 0.3, 0.3)  # grey
        self.sleep()    
    
    def recover_node(self):
        """Recover a failed node: wake up and rejoin network as UNREGISTERED."""
        log_event("NODE_RECOVER", self.id, role=str(getattr(self, "role", None)))
        self.failed = False
        self.wake_up()
        self.become_unregistered()



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

def export_cluster_tx_levels(path="cluster_tx_levels.csv"):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cluster_head", "tx_dbm", "range"])
        for node in sim.nodes:
            if getattr(node, "role", None) == Roles.CLUSTER_HEAD:
                w.writerow([node.id, getattr(node, "cluster_tx_dbm", ""), getattr(node, "tx_range", "")])               

def export_metrics_summary(path="metrics_summary.csv"):
    """
    one-line snapshot:
    time, nodes_registered, avg_join_time,
    tx_sent, tx_dropped, plr, rx_delivered,
    total_energy, avg_energy_per_packet
    """
    join_times = []
    try:
        with open("join_times.csv", "r", newline="") as f:
            r = csv.reader(f)
            for row in r:
                if not row or row[0] == "node_id":
                    continue
                try:
                    join_times.append(float(row[1]))
                except Exception:
                    pass
    except FileNotFoundError:
        pass

    avg_join = sum(join_times)/len(join_times) if join_times else 0.0

    tx_sent = tx_drop = rx_deliv = 0
    nodes_registered = 0
    total_energy = 0.0

    for node in sim.nodes:
        if getattr(node, "role", None) in (Roles.REGISTERED, Roles.CLUSTER_HEAD, Roles.ROOT):
            nodes_registered += 1
        tx_sent     += getattr(node, "tx_data_sent", 0)
        tx_drop     += getattr(node, "tx_data_dropped", 0)
        rx_deliv    += getattr(node, "rx_data_delivered", 0)
        total_energy += getattr(node, "energy_tx", 0.0) + getattr(node, "energy_rx", 0.0)

    plr = (tx_drop / tx_sent) if tx_sent else 0.0
    avg_energy_per_packet = (total_energy / tx_sent) if tx_sent else 0.0

    header_needed = False
    try:
        with open(path, "r") as _:
            pass
    except FileNotFoundError:
        header_needed = True

    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if header_needed:
            w.writerow(["time", "nodes_registered", "avg_join_time",
                        "tx_sent", "tx_dropped", "plr", "rx_delivered",
                        "total_energy", "avg_energy_per_packet"])
        w.writerow([f"{getattr(sim, 'time', 0.0):.3f}",
                    nodes_registered, f"{avg_join:.6f}",
                    tx_sent, tx_drop, f"{plr:.6e}", rx_deliv,
                    f"{total_energy:.6f}", f"{avg_energy_per_packet:.6f}"])


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

def log_event(event_type, node_id, **extra):
        """
        Append an event line to failure_events.csv
        Columns: time, event_type, node_id, <extra fields...>
        """
        global EVENTS_HEADER_WRITTEN
        field_names = ["time", "event_type", "node_id"] + list(extra.keys())
        row = [getattr(sim, "time", 0.0), event_type, node_id] + list(extra.values())

        path = "failure_events.csv"
        header_needed = False
        try:
            with open(path, "r") as _:
                pass
        except FileNotFoundError:
            header_needed = True

        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if header_needed:
                w.writerow(field_names)
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
        w.writerow(["node_id", "target_gui", "hop", "via_gui"])

        for node in sim.nodes:
            if getattr(config, "NEIGHBOR_SHARE_MAX_HOPS", 1) == 1:
                for tgt in getattr(node, "neighbors_table", {}).keys():
                    w.writerow([node.id, tgt, 1, tgt])

            for tgt, meta in getattr(node, "multi_neighbors", {}).items():
                w.writerow([node.id, tgt, meta.get("hop"),
                            meta.get("via")])


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
    if not globals().get("NODE_POS"):
        raise RuntimeError("NODE_POS is missing; record positions during create_network().")

    # Prepare a set to avoid duplicates if dedupe_undirected=True
    seen_pairs = set()

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "neighbor_id", "distance",
                    "neighbor_role", "neighbor_hop_count", "arrival_time"])

        for node in sim.nodes:
            if not hasattr(node, "neighbors_table"):
                continue

            x1, y1 = NODE_POS.get(node.id, (None, None))
            if x1 is None:
                continue

            for n_gui, pck in getattr(node, "neighbors_table", {}).items():
                if dedupe_undirected:
                    key = (min(node.id, n_gui), max(node.id, n_gui))
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)

                x2, y2 = NODE_POS.get(n_gui, (None, None))
                if x2 is None:
                    continue

                dist = pck.get("distance")
                if dist is None:
                    dist = math.hypot(x1 - x2, y1 - y2)

                n_role = getattr(pck.get("role", None), "name", pck.get("role", None))
                hop = pck.get("hop_count", "")
                at  = pck.get("arrival_time", "")

                w.writerow([node.id, n_gui, f"{dist:.6f}", n_role, hop, at])

def _range_for_dbm(dbm: int) -> float:
    steps = int(round(dbm / 6.0))
    return config.TX_RANGE_BASE * (config.TX_RANGE_SCALE_PER_6DB ** steps)

def _nearest_ch_distance(candidate_gui: int) -> float | None:
    """Return distance (in scene units) from candidate to nearest existing CH, or None if no CH exists yet."""
    if candidate_gui not in NODE_POS:
        return None
    cx, cy = NODE_POS[candidate_gui]
    best = None
    for n in sim.nodes:
        if getattr(n, "role", None) == Roles.CLUSTER_HEAD and n.id in NODE_POS and n.id != candidate_gui:
            x, y = NODE_POS[n.id]
            d = math.hypot(cx - x, cy - y)
            if best is None or d < best:
                best = d
    return best

def _is_core_node(gui: int) -> bool:
    """Return True if gui is currently ROOT or CLUSTER_HEAD."""
    for n in sim.nodes:
        if n.id == gui:
            return getattr(n, "role", None) in (Roles.ROOT, Roles.CLUSTER_HEAD)
    return False
            
def print_path_to_root(src_gui: int = None, *, label: str = "[ROUTE]", max_hops: int = 200, min_hops: int = 2):
    """
    Print the route from a node (or a random one) to the root using actual routing rules,
    preferring nodes whose parent chain to the root is at least `min_hops` long.

    Usage:
        print_path_to_root(min_hops=3)     # pick a node >=3 hops from root
        print_path_to_root(47, min_hops=2) # force a specific source (min_hops ignored)
    """
    root_id = config.ROOT_ID
    nodes = {n.id: n for n in sim.nodes}

    def hop_distance_to_root(node_id: int) -> int | None:
        """Count hops following parent_gui → root. Return None if not connected/loop."""
        seen = set()
        cur = nodes.get(node_id)
        hops = 0
        while cur and cur.id != root_id:
            if cur.id in seen:
                return None
            seen.add(cur.id)
            p = getattr(cur, "parent_gui", None)
            if p is None:
                return None
            cur = nodes.get(p)
            hops += 1
            if hops > max_hops:
                return None
        return hops if cur and cur.id == root_id else None

    # Choose source
    if src_gui is None:
        # Prefer nodes that are >= min_hops away via the tree-parent chain
        candidates = []
        for n in sim.nodes:
            if n.id == root_id: 
                continue
            if getattr(n, "role", None) not in (Roles.REGISTERED, Roles.CLUSTER_HEAD):
                continue
            h = hop_distance_to_root(n.id)
            if h is not None and h >= min_hops:
                candidates.append((h, n.id))
        # If none meet min_hops, fall back to any connected node with h>=1
        if not candidates:
            fallback = []
            for n in sim.nodes:
                if n.id == root_id:
                    continue
                if getattr(n, "role", None) not in (Roles.REGISTERED, Roles.CLUSTER_HEAD):
                    continue
                h = hop_distance_to_root(n.id)
                if h is not None and h >= 1:
                    fallback.append((h, n.id))
            candidates = fallback

        if not candidates:
            print(f"{label} No eligible nodes yet — network still forming?")
            return

        # Pick the farthest to maximize chance of multi-hop routing
        candidates.sort(reverse=True)  # farthest first
        src_gui = candidates[0][1]

    if src_gui not in nodes:
        print(f"{label} start node {src_gui} not found")
        return
    if root_id not in nodes:
        print(f"{label} root {root_id} not found")
        return

    cur = nodes[src_gui]
    path = [cur.id]
    modes = []
    prev = None

    for _ in range(max_hops):
        if cur.id == root_id:
            break

        nh_gui, mode = cur.choose_next_hop(root_id, prev)

        # If routing doesn’t give us a next hop, walk the tree parent chain as a fallback
        if nh_gui is None:
            # Construct a pure-tree suffix
            tree_chain = []
            walker = cur
            seen = set(path)
            while walker and getattr(walker, "parent_gui", None) is not None and len(path) + len(tree_chain) < max_hops:
                p = walker.parent_gui
                if p in seen:
                    break
                tree_chain.append(p)
                seen.add(p)
                walker = nodes.get(p)
                if not walker or p == root_id:
                    break
            full_path = path + tree_chain
            print(f"{label} {src_gui} -> {root_id} : {'->'.join(map(str, full_path))}  (modes={modes + ['tree'] * len(tree_chain)})")
            return

        # guard simple bounce/loop
        if nh_gui in path[-2:]:
            print(f"{label} loop near {cur.id}->{nh_gui}. So far: {'->'.join(map(str, path))}")
            return

        modes.append(mode)
        prev = cur.id
        cur = nodes.get(nh_gui)
        if cur is None:
            print(f"{label} next hop {nh_gui} missing")
            return
        path.append(cur.id)

    if path[-1] != root_id:
        print(f"{label} reached hop limit. Path so far: {'->'.join(map(str, path))}")
        return
    print(f"{label} {src_gui} -> {root_id} : {'->'.join(map(str, path))}  (modes={modes})")
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
        px = 300 + config.SCALE * x * config.SIM_NODE_PLACING_CELL_SIZE + random.uniform(-config.SIM_NODE_PLACING_CELL_SIZE/3, config.SIM_NODE_PLACING_CELL_SIZE/3)
        py = 200 + config.SCALE * y * config.SIM_NODE_PLACING_CELL_SIZE + random.uniform(-config.SIM_NODE_PLACING_CELL_SIZE/3, config.SIM_NODE_PLACING_CELL_SIZE/3)

        node = sim.add_node(node_class, (px, py))
        NODE_POS[node.id] = (px, py)

        # neutral defaults before cluster assignment
        node.tx_level_dbm = 0
        node.tx_range = _range_for_dbm(0) * config.SCALE

        node.logging = True
        node.arrival = random.uniform(0, config.NODE_ARRIVAL_MAX)
        node.t_created = getattr(sim, "time", 0.0)

        if node.id == config.ROOT_ID:
            node.arrival = 0.1
def root_seed_new_ch():
    """Root periodically selects a far-enough node and promotes it to CH."""
    root = next((n for n in sim.nodes if getattr(n, "role", None) == Roles.ROOT), None)
    if not root:
        return

    # Gate (eligibility) power – consistent with CH-GATE and CH-SELF checks
    GATE_DBM = 0
    cand_rng = _range_for_dbm(GATE_DBM) * config.SCALE

    # Transmission power after promotion (coverage of new CH)
    prom_dbm = getattr(config, "CH_PROMOTION_DBM", +6)

    sep_mode = getattr(config, "CH_SEPARATION_MODE", "candidate")
    factor = getattr(config, "CH_MIN_SEPARATION_FACTOR", 1.0)
    deny_cd = getattr(config, "CH_DENY_COOLDOWN", 30)
    retry_cd = getattr(config, "ROOT_SEED_RETRY_COOLDOWN", 10)
    now = getattr(sim, "time", 0.0)

    def sep_radius(nearest_rng):
        return max(cand_rng, nearest_rng) if sep_mode == "max" else cand_rng

    best_gui, best_d = None, -1.0
    best_near_rng = 0.0

    for n in sim.nodes:
        # Only consider REGISTERED nodes that exist in NODE_POS
        if getattr(n, "role", None) != Roles.REGISTERED or n.id not in NODE_POS:
            continue

        # Skip nodes recently denied or recently offered
        last_deny = LAST_CH_DENY_AT.get(n.id)
        if last_deny and (now - last_deny) < deny_cd:
            continue
        last_offer = ROOT_SEED_COOLDOWN.get(n.id, -1e9)
        if (now - last_offer) < retry_cd:
            continue

        nx, ny = NODE_POS[n.id]
        nearest_d = float("inf")
        nearest_rng = 0.0
        for ch in sim.nodes:
            if getattr(ch, "role", None) == Roles.CLUSTER_HEAD and ch.id in NODE_POS:
                cx, cy = NODE_POS[ch.id]
                d = math.hypot(nx - cx, ny - cy)
                if d < nearest_d:
                    nearest_d = d
                    nearest_rng = getattr(ch, "tx_range", 0.0)

        # Evaluate CH gate geometry
        if nearest_d == float("inf"):
            passes = True
        else:
            min_required = factor * sep_radius(nearest_rng)
            passes = (nearest_d >= min_required)

        if passes and nearest_d > best_d:
            best_d, best_gui, best_near_rng = nearest_d, n.id, nearest_rng

    # No candidate found – apply relaxation strategy
    if best_gui is None:
        root._seed_fail_count = getattr(root, "_seed_fail_count", 0) + 1
        if root._seed_fail_count in (3, 6):
            if root._seed_fail_count == 3:
                print("[ROOT-SEED] no eligible candidates; temporarily relaxing CH_MIN_SEPARATION_FACTOR by 10%")
                config.CH_MIN_SEPARATION_FACTOR = max(0.3, config.CH_MIN_SEPARATION_FACTOR * 0.9)
            else:
                print("[ROOT-SEED] still stuck; bumping CH_PROMOTION_DBM to +6 dBm this cycle")
                config.CH_PROMOTION_DBM = +6
        return

    # Success: reset fail count and record cooldown
    root._seed_fail_count = 0
    ROOT_SEED_COOLDOWN[best_gui] = now

    # Send promotion message
    new_addr = wsn.Addr(config.NETWORK_ID, best_gui)
    root.route_and_forward_package({
        'dest': wsn.BROADCAST_ADDR,
        'type': 'NETWORK_REPLY',
        'source': root.addr,
        'addr': new_addr,
        'gui': best_gui
    })
    print(f"[ROOT-SEED] offered promotion to node {best_gui} "
          f"(nearestCH={best_d:.1f}, near_rng={best_near_rng:.1f})") 

def optimize_clusters():
    """
    Simple cluster optimization:
    - Find CLUSTER_HEADs with very few members and close to another CH.
    - If all their members are within range of a neighbor CH, demote this CH.
    This reduces the total number of clusters over time.
    """
    min_children = getattr(config, "CH_MIN_CHILDREN_FOR_KEEP", 3)
    max_merge_dist_factor = getattr(config, "CH_MERGE_MAX_DIST_FACTOR", 1.2)

    ch_nodes = [n for n in sim.nodes
                if getattr(n, "role", None) == Roles.CLUSTER_HEAD and n.id in NODE_POS]

    if len(ch_nodes) < 2:
        return  

    used = set()

    for ch in ch_nodes:
        if ch.id in used:
            continue

        members = getattr(ch, "members_table", [])
        if len(members) >= min_children:
            continue

        cx, cy = NODE_POS[ch.id]
        nearest = None
        best_d = None

        for other in ch_nodes:
            if other.id == ch.id or other.id in used or other.id not in NODE_POS:
                continue
            ox, oy = NODE_POS[other.id]
            d = math.hypot(cx - ox, cy - oy)
            if best_d is None or d < best_d:
                best_d = d
                nearest = other

        if nearest is None or best_d is None:
            continue

        ref_range = getattr(nearest, "tx_range",
                            _range_for_dbm(getattr(nearest, "cluster_tx_dbm", 6)) * config.SCALE)
        if best_d > max_merge_dist_factor * ref_range:
            continue

        ok = True
        for m_gui in members:
            if m_gui not in NODE_POS or nearest.id not in NODE_POS:
                ok = False
                break
            mx, my = NODE_POS[m_gui]
            nx, ny = NODE_POS[nearest.id]
            dmn = math.hypot(mx - nx, my - ny)
            if dmn > ref_range:
                ok = False
                break

        if not ok:
            continue

        print(f"[CLUSTER-OPT] Demoting CH {ch.id} in favor of CH {nearest.id}")
        log_event("DEMOTE_CH", ch.id, new_parent=nearest.id)

        used.add(ch.id)
        used.add(nearest.id)

        ch.ch_addr = None
        ch.set_role(Roles.REGISTERED)
        ch.become_unregistered()  


sim = wsn.Simulator(
    duration=config.SIM_DURATION,
    timescale=config.SIM_TIME_SCALE,
    visual=config.SIM_VISUALIZATION,
    terrain_size=config.SIM_TERRAIN_SIZE,
    title=config.SIM_TITLE)
def generate_sleep_cycles(node_ids, min_death, max_death, min_wakeup_delay, max_wakeup_delay):
    cycles = {}
    for nid in node_ids:
        death = random.uniform(min_death, max_death)
        wakeup_delay = random.uniform(min_wakeup_delay, max_wakeup_delay)
        wakeup = death + wakeup_delay
        cycles[nid] = {"death_time": death, "wakeup_time": wakeup}
    return cycles
print("Config",
      "TX_RANGE_BASE=", config.TX_RANGE_BASE,
      "SCALE=", config.SCALE,
      "NEIGHBOR_SHARE_MAX_HOPS=", config.NEIGHBOR_SHARE_MAX_HOPS,
      "LOCAL_MESH_MAX_HOPS(eff)=", config.LOCAL_MESH_MAX_HOPS())

def debug_dump_unconnected():
    """
    After the run, print why nodes failed to join:
    - role
    - distance to nearest CH or ROOT
    """
    print("\n=== DEBUG: Unconnected nodes summary ===")
    for n in sim.nodes:
        role = getattr(n, "role", None)
        if role in (Roles.REGISTERED, Roles.CLUSTER_HEAD, Roles.ROOT, Roles.Router):
            continue

        # distance to nearest CH/ROOT (for geometry)
        if n.id not in NODE_POS:
            print(f"Node {n.id}: role={role.name if role else role}, no position.")
            continue

        x, y = NODE_POS[n.id]
        best = None
        for m in sim.nodes:
            if getattr(m, "role", None) in (Roles.ROOT, Roles.CLUSTER_HEAD) and m.id in NODE_POS:
                x2, y2 = NODE_POS[m.id]
                d = math.hypot(x - x2, y - y2)
                if best is None or d < best:
                    best = d

        dist_str = "inf" if best is None else f"{best:.1f}"
        print(f"frfeefewwe {n.id:2d}: role={role.name if role else role:13s}  d_nearest_CH/ROOT={dist_str}")
    print("=== end debug ===\n")

# creating random network
create_network(SensorNode, config.SIM_NODE_COUNT)
kill_candidates = [n.id for n in sim.nodes if n.id != config.ROOT_ID]
random.shuffle(kill_candidates)
kill_subset = kill_candidates[:min(10, len(kill_candidates))]

MIN_DEATH = config.NODE_ARRIVAL_MAX          # after they should have arrived
MAX_DEATH = MIN_DEATH * 3
MIN_WAKEUP_DELAY = 50                        # how long they stay dead
MAX_WAKEUP_DELAY = 150

KILL_AND_WAKEUP = generate_sleep_cycles(
    kill_subset, MIN_DEATH, MAX_DEATH, MIN_WAKEUP_DELAY, MAX_WAKEUP_DELAY
)

for node in sim.nodes:
    if node.id in KILL_AND_WAKEUP:
        cycle = KILL_AND_WAKEUP[node.id]
        node.set_timer("TIMER_FAIL_NODE", cycle["death_time"])
        node.set_timer("TIMER_RECOVER_NODE", cycle["wakeup_time"])

write_node_distances_csv("node_distances.csv")
write_node_distance_matrix_csv("node_distance_matrix.csv")

# start the simulation
sim.run()
export_metrics_summary()
export_cluster_tx_levels()
write_clusterhead_distances_csv()
write_neighbor_distances_csv()
write_multihop_csv()
print_path_to_root(src_gui=67)
debug_dump_unconnected()
print("CH gate config:",
      "PROMO_DBM=", config.CH_PROMOTION_DBM,
      "MODE=", config.CH_SEPARATION_MODE,
      "FACTOR=", config.CH_MIN_SEPARATION_FACTOR)
print("Simulation Finished")


# Created 100 nodes at random locations with random arrival times.
# When nodes are created they appear in white
# Activated nodes becomes red
# Discovered nodes will be yellow
# Registered nodes will be green.
# Root node will be black.
# Routers/Cluster Heads should be blue
