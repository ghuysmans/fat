import sys
import struct
import os

class BPB(object):
    def __init__(self, partition):
        self.p = partition
        self.formatter = self.p.read(3, 8)
        #FIXME merge? then disable padding
        self.bytes_per_sector = self.p.read_s(11, "<H")[0]
        self.sectors_per_cluster = self.p.read_s(13, "B")[0]
        self.reserved_sectors = self.p.read_s(14, "<H")[0]
        self.fats = self.p.read_s(16, "B")[0]
        self.root_entries = self.p.read_s(17, "<H")[0]
        self.disk_type = self.p.read_s(21, "B")[0] #FIXME enum
        self.sectors_per_fat = self.p.read_s(22, "<H")[0]
        self.hidden_sectors = self.p.read_s(28, "<I")[0] #FIXME useful?
        #FIXME read other useful fields
        #FIXME test FAT16/FAT32 (signatures) and create the right instance

class FAT16(object):
    def __init__(self, bpb, i):
        self.p = bpb.p
        self._start = (bpb.reserved_sectors + i * bpb.sectors_per_fat) * bpb.bytes_per_sector
        root = (bpb.reserved_sectors + bpb.fats * bpb.sectors_per_fat) * bpb.bytes_per_sector
        root_size = bpb.root_entries * 32
        self._data = root + root_size
        self.cluster_size = bpb.sectors_per_cluster * bpb.bytes_per_sector
        self.root = Directory(self, Contiguous(bpb.p, root, root_size))
    def data(self, cluster):
        if cluster >= 2 and cluster < 0xFFEF:
            return self._data + (cluster - 2) * self.cluster_size
    def next(self, cluster):
        return self.p.read_s(self._start + cluster * 2, "<H")[0]

class File(object):
    def __init__(self, fat, first, size=None):
        l = [first]
        while True:
            next = fat.next(l[-1])
            if next >= 0xFFF8:
                break #last
            elif next >= 2 and next < 0xFFEF:
                l.append(next)
            else:
                raise IOError("invalid cluster in chain")
        self._fat = fat
        self._clusters = l
        if size == None:
            self.size = len(l) * self._fat.cluster_size
        else:
            self.size = size
    def read(self, ofs, len):
        if ofs < 0:
            raise TypeError("negative offset")
        elif len < 0:
            raise TypeError("negative length")
        elif ofs + len >= self.size:
            raise IOError("out of bounds read")
        cluster = self._clusters[ofs // self._fat.cluster_size]
        ofs &= self._fat.cluster_size - 1
        return self._fat.p.read(self._fat.data(cluster) + ofs, len)
    def read_s(self, ofs, fmt):
        return struct.unpack(fmt, self.read(ofs, struct.calcsize(fmt)))

class Entry(object):
    def __init__(self, fat, data, ofs):
        self._fat = fat
        self.deleted = False
        raw = data.read(ofs, 11)
        if raw[0] == '\x00':
            self.name = "" #FIXME move this into Directory and return None
        elif raw[0] == '.':
            self.name = raw.strip()
        else:
            if raw[0] == '\x05':
                raw[0] = '\xe5'
            elif raw[0] == '\xe5':
                raw[0] = '?'
                self.deleted = True
            self.name = raw[:8].strip()
            if raw[8:] != "   ":
                self.name += "." + raw[8:].strip()
        self.attributes = data.read_s(ofs + 0xb, "B")[0]
        self.first = data.read_s(ofs + 0x1a, "<H")[0]
        self.size = data.read_s(ofs + 0x1c, "<H")[0]
    def open(self):
        if self.attributes & 0x10:
            return Directory(self._fat, File(self._fat, self.first))
        elif self.attributes == 0x0f:
            raise TypeError("can't open an LFN entry")
        else:
            return File(self._fat, self.first)

#FIXME define a Data interface
class Contiguous(object):
    def __init__(self, data, ofs, size):
        self._data = data
        self._ofs = ofs
        self.size = size
    def read(self, ofs, len):
        if ofs < 0:
            raise TypeError("negative offset")
        if len < 0:
            raise TypeError("negative length")
        elif len >= self.size:
            raise IOError("out of bounds read")
        return self._data.read(self._ofs + ofs, len)
    def read_s(self, ofs, fmt):
        return struct.unpack(fmt, self.read(ofs, struct.calcsize(fmt)))

class Image(object):
    def __init__(self, fn):
        self._f = open(fn, "rb")
    def read(self, ofs, len):
        self._f.seek(ofs)
        return self._f.read(len)
    def __len__(self):
        return os.fstat(self._f.fileno()).st_size
    def __enter__(self):
        return self
    def __exit__(self, type, value, tb):
        self._f.close()

class Directory(object):
    def __init__(self, fat, data):
        self._fat = fat
        self._data = data
        self._count = data.size // 32
    #TODO implement an iterator for LFN?
    def __len__(self):
        return self._count
    def __getitem__(self, i):
        if i >= 0 and i < self._count:
            return Entry(self._fat, self._data, i * 32)
        else:
            raise IndexError("invalid directory entry index")


with Image(sys.argv[1]) as f:
    part = Contiguous(f, 0, len(f))
    bpb = BPB(part)
    fat = FAT16(bpb, 0) #the first one
    for e in fat.root:
        if len(e.name) and e.attributes != 0x0f:
            print(e.first, hex(fat.data(e.first)), e.name)
            #print(e.open().read(0, 100))
#TODO nested volumes :D
