import struct

SYS = {"libc.so", "libm.so", "libdl.so", "liblog.so", "libz.so", "libandroid.so", "libstdc++.so", "ld-android.so", "libEGL.so", "libGLESv2.so", "libvulkan.so", "libnativewindow.so", "libOpenSLES.so", "libjnigraphics.so"}


def elf(data):
    if data[:4] != b"\x7fELF":
        return None
    if data[4] != 2 or data[5] != 1:
        return {"note": "not 64-bit little endian"}
    etype, machine = struct.unpack_from("<HH", data, 16)
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    interp = None
    dyn = None
    loads = []
    for i in range(phnum):
        pt, fl, off, va, pa, fs, ms, al = struct.unpack_from("<IIQQQQQQ", data, phoff + i * phentsize)
        if pt == 3:
            interp = data[off:off + fs].split(b"\0")[0].decode()
        elif pt == 2:
            dyn = (off, fs)
        elif pt == 1:
            loads.append((va, off, fs))
    needed, rpath = [], []
    if dyn:
        ents = []
        for k in range(0, dyn[1], 16):
            tag, val = struct.unpack_from("<qQ", data, dyn[0] + k)
            if tag == 0:
                break
            ents.append((tag, val))
        st = [v for t, v in ents if t == 5]
        so = None
        if st:
            for va, off, fs in loads:
                if va <= st[0] < va + fs:
                    so = off + (st[0] - va)
        if so is not None:
            def s(i):
                return data[so + i:data.index(b"\0", so + i)].decode(errors="replace")
            for t, v in ents:
                if t == 1:
                    needed.append(s(v))
                elif t in (15, 29):
                    rpath.append(s(v))
    return {"machine": machine, "type": etype, "interp": interp, "static": dyn is None, "needed": needed, "rpath": rpath}


def validate(path, provided=()):
    with open(path, "rb") as fh:
        data = fh.read()
    e = elf(data)
    if e is None:
        raise RuntimeError(path + " is not an ELF file (first bytes " + repr(data[:8]) + ")")
    if e.get("machine") != 183:
        raise RuntimeError(path + " is not aarch64 (machine " + str(e.get("machine")) + ", note " + str(e.get("note")) + ")")
    bad = [n for n in e["needed"] if n not in SYS and n not in provided]
    if e["interp"] and e["interp"] not in ("/system/bin/linker64",) and "linker64" not in e["interp"]:
        raise RuntimeError(path + " expects the dynamic loader " + e["interp"] + " which Android does not have. This is a desktop Linux (glibc) build, not an Android build.")
    if bad:
        raise RuntimeError(path + " needs libraries that are not on Android and not bundled: " + ", ".join(bad))
    return e
