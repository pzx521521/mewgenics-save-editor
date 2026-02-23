import struct
import sqlite3
import lz4.block
import json

# 属性名常量
STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]


class BinaryReader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def u32(self):
        val = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return val

    def i32(self):
        val = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return val

    def u64(self):
        low, high = struct.unpack_from('<II', self.data, self.pos)
        self.pos += 8
        return low + (high * 4294967296)

    def i64(self):
        low, high = struct.unpack_from('<Ii', self.data, self.pos)
        self.pos += 8
        return low + (high * 4294967296)

    def f64(self):
        val = struct.unpack_from('<d', self.data, self.pos)[0]
        self.pos += 8
        return val

    def str(self):
        start = self.pos
        try:
            length = self.u64()
            if length > 10000 or length < 0: return None
            res = self.data[self.pos: self.pos + int(length)].decode('utf-8', errors='ignore')
            self.pos += int(length)
            return res
        except:
            self.pos = start
            return None

    def utf16str(self):
        char_count = self.u64()
        byte_len = int(char_count * 2)
        res = self.data[self.pos: self.pos + byte_len].decode('utf-16le', errors='ignore')
        self.pos += byte_len
        return res

    def skip(self, n):
        self.pos += n

    def seek(self, n):
        self.pos = n

    def remaining(self):
        return len(self.data) - self.pos


class Cat:
    def __init__(self, blob, cat_key, house_info):
        # 1. 解压 LZ4
        uncompressed_size = struct.unpack('<I', blob[:4])[0]
        self.decompressed_data = lz4.block.decompress(blob[4:], uncompressed_size=uncompressed_size)
        reader = BinaryReader(self.decompressed_data)

        # 记录基础信息
        self.db_key = cat_key
        self.inHouse = cat_key in house_info
        self.room = house_info.get(cat_key, None)

        # 顺序解析字段
        self.breedId = reader.u32()
        self.uniqueId = hex(reader.u64())
        self.name = reader.utf16str()

        reader.str()  # skip unknown
        reader.skip(16)
        self.collar = reader.str()
        reader.u32()

        # 身体部件
        reader.skip(64)  # 跳过 internalStats
        T = [reader.u32() for _ in range(72)]
        # 简化 BodyParts 输出
        self.bodyParts = {"texture": T[0], "bodyShape": T[3], "headShape": T[8]}
        reader.skip(12)

        self.gender = reader.str()
        reader.f64()

        # 核心属性
        self.statAllocations = [reader.u32() for _ in range(7)]
        self.statModifiers = [reader.i32() for _ in range(7)]
        self.statSecondary = [reader.i32() for _ in range(7)]

        self.stats = {}
        for i, name in enumerate(STAT_NAMES):
            self.stats[name] = self.statAllocations[i] + self.statModifiers[i] + self.statSecondary[i]

        # 技能搜索
        curr = reader.pos
        found_abil = -1
        for i in range(curr, min(curr + 500, len(self.decompressed_data) - 9)):
            length = struct.unpack_from('<I', self.decompressed_data, i)[0]
            if 0 < length < 64 and struct.unpack_from('<I', self.decompressed_data, i + 4)[0] == 0:
                if 65 <= self.decompressed_data[i + 8] <= 90:
                    found_abil = i
                    break

        if found_abil != -1: reader.seek(found_abil)
        self.abilities = [reader.str() for _ in range(6)]
        self.abilities = [a for a in self.abilities if a]

        # 被动
        self.equipmentSlots = [reader.str() for _ in range(4)]
        self.passives = []
        p1 = reader.str()
        if p1: self.passives.append(p1)
        for _ in range(3):
            if reader.remaining() >= 12:
                reader.u32()  # flag
                p = reader.str()
                if p: self.passives.append(p)

    def to_dict(self):
        d = vars(self).copy()
        if 'decompressed_data' in d: del d['decompressed_data']
        if 'statAllocations' in d: del d['statAllocations']
        if 'statModifiers' in d: del d['statModifiers']
        if 'statSecondary' in d: del d['statSecondary']
        return d


def get_house_info(conn):
    """对应 JS 的 hg 函数：解析房间里的猫"""
    house_info = {}
    row = conn.execute("SELECT data FROM files WHERE key = 'house_state'").fetchone()
    if not row: return house_info

    data = row[0]
    if len(data) < 8: return house_info

    # 获取猫的数量
    count = struct.unpack_from('<I', data, 4)[0]
    pos = 8
    for _ in range(count):
        if pos + 8 > len(data): break
        cat_key = struct.unpack_from('<I', data, pos)[0]
        pos += 8

        room_len = struct.unpack_from('<I', data, pos)[0]
        pos += 8

        room_name = ""
        if room_len > 0:
            room_name = data[pos:pos + room_len].decode('ascii', errors='ignore')
            pos += room_len

        pos += 24  # 固定偏移填充
        house_info[cat_key] = room_name

    return house_info


def parse_all(path):
    conn = sqlite3.connect(path)
    # 1. 先拿房间信息
    house_info = get_house_info(conn)

    # 2. 拿所有猫
    rows = conn.execute("SELECT key, data FROM cats").fetchall()
    cats = []
    for k, b in rows:
        try:
            cat_obj = Cat(b, k, house_info)
            # 3. 如果你想只输出有用的猫，取消下面注释：
            if not cat_obj.inHouse: continue
            cats.append(cat_obj.to_dict())
        except:
            continue

    conn.close()
    return cats


if __name__ == "__main__":
    cats = parse_all('steamcampaign02.sav.txt')
    print(json.dumps(cats, indent=4, ensure_ascii=False))