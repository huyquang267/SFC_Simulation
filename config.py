# Đơn vị: thời gian = giây, băng thông = Mbps

EDGE_CLUSTERS = 4
DEVICES_PER_CLUSTER = 75
CAMERAS_PER_DEVICE = 7

MAX_SFC = (
    EDGE_CLUSTERS
    * DEVICES_PER_CLUSTER
    * CAMERAS_PER_DEVICE
)  # 2100

EDGE_INGRESS = ["N5", "N12", "N13", "N15"]
CLOUD_INGRESS = "N1"

EDGE_LINK_MBPS = 100.0
CORE_LINK_MBPS = 10000.0
DC_LINK_MBPS = 10000.0

FAT_TREE_K = 10
CLOUD_SERVERS = FAT_TREE_K ** 3 // 4  # 250

MEAN_SERVICE_TIME_S = 3600.0

CNF_ORDER = ["Cap", "Dec", "Den", "LoS"]

# CPU tính theo điểm phần trăm trên thiết bị tương ứng
CPU_EDGE = {
    "Cap": 3.0,
    "Dec": 8.0,
    "Den": 13.6,
    "LoS": 25.0,
}

CPU_CLOUD = {
    "Cap": None,  # Cap chỉ đặt ở edge
    "Dec": 1.5,
    "Den": 6.5,
    "LoS": 5.0,
}

STRATEGIES = ["cloud", "edge_cloud", "edge_cloud_eo"]