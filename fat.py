#!/usr/bin/env python3
import sys
import struct
import os
from abc import abstractmethod

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
        self._data_start = root + root_size
        self.cluster_size = bpb.sectors_per_cluster * bpb.bytes_per_sector
        self.root = Directory(self, Contiguous(bpb.p, root, root_size))
    def data(self, cluster):
        if cluster >= 2 and cluster < 0xFFEF:
            return self._data_start + (cluster - 2) * self.cluster_size
    def get_next(self, cluster):
        return self.p.read_s(self._start + cluster * 2, "<H")[0]
    def set_next(self, cluster, next):
        return self.p.write_s(self._start + cluster * 2, "<H", next)

class Data(object):
    def __init__(self, data, size):
        self._data = data
        self.size = size
    @abstractmethod
    def _translate(self, ofs, len):
        pass
    def _check_access(self, ofs, len):
        if ofs < 0:
            raise TypeError("negative offset")
        if len < 0:
            raise TypeError("negative length")
    def read(self, ofs, len):
        self._check_access(ofs, len)
        return self._data.read(self._translate(ofs, len), len)
    def read_s(self, ofs, fmt):
        return struct.unpack(fmt, self.read(ofs, struct.calcsize(fmt)))
    def write(self, ofs, data):
        self._check_access(ofs, len(data))
        return self._data.write(self._translate(ofs, len(data)), data)
    def write_s(self, ofs, fmt, *data):
        return self.write(ofs, struct.pack(fmt, *data))

class File(Data):
    def __init__(self, fat, first, size=None):
        l = [first]
        while True:
            next = fat.get_next(l[-1])
            if next >= 0xFFF8:
                break #last
            elif next >= 2 and next < 0xFFEF:
                l.append(next)
            else:
                raise IOError("invalid cluster in chain")
        self._fat = fat
        self._clusters = l
        if size == None:
            size = len(l) * self._fat.cluster_size
        Data.__init__(self, fat.p, size)
    def _translate(self, ofs, len):
        if ofs + len >= self.size:
            raise IOError("out of bounds access")
        cluster = self._clusters[ofs // self._fat.cluster_size]
        ofs &= self._fat.cluster_size - 1
        return self._fat.data(cluster) + ofs

DIRECTORY = 0x10
LFN = 0x0f

class Entry(object):
    def __init__(self, fat, data, ofs):
        self._fat = fat
        self._ofs = ofs
        self._data = data
        self.deleted = False
        self.attributes = data.read_s(ofs + 0xb, "B")[0]
        raw = data.read(ofs, 11)
        if raw[0] == 0 or self.attributes & LFN:
            self.name = "" #FIXME move this into Directory and return None
        elif raw[0] == ord('.'):
            self.name = raw.decode("ascii").strip()
        else:
            start = 0
            if raw[0] == 0x05:
                raw = b"\xe5" + raw[1:]
            elif raw[0] == 0xe5:
                start = 1
                self.deleted = True
            self.name = raw[start:8].decode("ascii").strip()
            if raw[8:] != b"   ":
                self.name += "." + raw[8:].decode("ascii").strip()
        self.first = data.read_s(ofs + 0x1a, "<H")[0]
        self.size = data.read_s(ofs + 0x1c, "<H")[0]
    def open(self):
        if self.attributes & DIRECTORY:
            return Directory(self._fat, File(self._fat, self.first))
        elif self.attributes == LFN:
            raise TypeError("can't open an LFN entry")
        else:
            return File(self._fat, self.first)
    def set_first(self, cluster):
        self.first = cluster
        self._data.write_s(self._ofs + 0x1a, "<H", cluster)

class Contiguous(Data):
    def __init__(self, data, ofs, size):
        Data.__init__(self, data, size)
        self._ofs = ofs
    def _translate(self, ofs, len):
        if ofs + len >= self.size: #that was a bug
            raise IOError("out of bounds read")
        return self._ofs + ofs

class Image(object):
    def __init__(self, fn, write=False):
        if write:
            mode = "r+b"
        else:
            mode = "rb"
        self._f = open(fn, mode)
    def read(self, ofs, len):
        self._f.seek(ofs)
        return self._f.read(len)
    def write(self, ofs, data):
        self._f.seek(ofs)
        return self._f.write(data)
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
    def get(self, name):
        for e in self:
            if e.name == name:
                return e
    #TODO implement an iterator for LFN?
    def __len__(self):
        return self._count
    def __getitem__(self, i):
        if i >= 0 and i < self._count:
            return Entry(self._fat, self._data, i * 32)
        else:
            raise IndexError("invalid directory entry index")

def do_dir(dir):
    print("deleted", "first", "addr", "name", sep="\t")
    for e in dir:
        if len(e.name) and e.attributes != LFN:
            data = fat.data(e.first)
            if data != None:
                data = hex(data)
            d = "D" if e.deleted else ""
            suffix = "/" if e.attributes & DIRECTORY else ""
            print(d, e.first, data, e.name + suffix, sep="\t")
            if e.name == "ROUTES.BAT":
                e.open().write(0, "echo Hello, World !\r\n")
            #print(e.open().read(0, 100))
            """
            if e.name == "BIS":
                e.set_first(18)
            """
            """
            #nested volumes :D
            if e.name == "MINI.IMG":
                p2 = e.open()
                b2 = BPB(p2)
                f2 = FAT16(b2, 0)
                for e in f2.root:
                    if len(e.name):
                        print(e.name)
            """

def do_hack(root):
    d = root.get("D")
    df = d.open()
    d2 = root.get("D2")
    d2f = d2.open()
    if len(sys.argv) == 2 and sys.argv[1] == "--write":
        df.get("AUTRE").set_first(d2.first)
        d2f.get("AUTRE").set_first(d.first)


with Image(sys.argv[1], write=True) as f:
    part = Contiguous(f, 0, len(f))
    bpb = BPB(part)
    fat = FAT16(bpb, 0) #the first one
    do_hack(fat.root)
