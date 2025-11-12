## network properties
BROADCAST_NET_ADDR = 255
BROADCAST_NODE_ADDR = 255

ROOT_ID = 1
NETWORK_ID = 1
ROOT_SHORT_ADDR = 254
BROADCAST_ADDR = 255 
MAX_CHILDREN_PER_CLUSTER = 5
NEIGHBOR_SHARE_MAX_HOPS = 3
NEIGHBOR_SHARE_INTERVAL = 5


## node properties
NODE_TX_RANGE = 100  # transmission range of nodes
NODE_ARRIVAL_MAX = 200  # max time to wake up


## simulation properties
SIM_NODE_COUNT = 100  # noce count in simulation
SIM_NODE_PLACING_CELL_SIZE = 75  # cell size to place one node
SIM_DURATION = 5000  # simulation Duration in seconds
SIM_TIME_SCALE = 0.00001  #  The real time dureation of 1 second simualtion time
SIM_TERRAIN_SIZE = (1400, 1400)  #terrain size
SIM_TITLE = 'Data Collection Tree'  # title of visualization window
SIM_VISUALIZATION = True  # visualization active
SCALE = 1  # scale factor for visualization


## application properties
HEARTH_BEAT_TIME_INTERVAL = 100
REPAIRING_METHOD = 'FIND_ANOTHER_PARENT' # 'ALL_ORPHAN', 'FIND_ANOTHER_PARENT'
EXPORT_CH_CSV_INTERVAL = 10  # simulation time units;
EXPORT_NEIGHBOR_CSV_INTERVAL = 10  # simulation time units;