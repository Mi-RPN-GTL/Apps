#!/usr/bin/env python3
import hashlib, io, json, lzma, os, subprocess, sys, tarfile

USAGE = """pack_factory.py keygen KEYDIR
  makes KEYDIR/catalog_key.pem, prints the catalogPubKey line for maker.properties
pack_factory.py build SRCDIR OUTDIR ID VERSION NAME [--requires a,b] [--entry tool=rel/path ...]
  packs SRCDIR into OUTDIR/ID-VERSION.tar.xz plus OUTDIR/ID.info.json
pack_factory.py catalog OUTDIR KEYDIR BASEURL
  writes OUTDIR/catalog.json signed with KEYDIR/catalog_key.pem (needs openssl)
Archives are USTAR tar + xz with pack.json at the root. Upload files as release assets."""


def run(cmd, data=None):
    r = subprocess.run(cmd, input=data, capture_output=True)
    if r.returncode != 0:
        sys.exit(r.stderr.decode(errors="replace"))
    return r.stdout


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def b64(b):
    import base64
    return base64.b64encode(b).decode()


def pubkey(pem):
    return b64(run(["openssl", "ec", "-in", pem, "-pubout", "-outform", "DER"]))


def keygen(keydir):
    os.makedirs(keydir, exist_ok=True)
    pem = os.path.join(keydir, "catalog_key.pem")
    if os.path.exists(pem):
        sys.exit("key already exists: " + pem)
    run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", pem])
    os.chmod(pem, 0o600)
    print("catalogPubKey=" + pubkey(pem))


def build(src, out, pid, version, name, rest):
    requires = []
    entries = {}
    i = 0
    while i < len(rest):
        if rest[i] == "--requires" and i + 1 < len(rest):
            requires = [x for x in rest[i + 1].split(",") if x]
            i += 2
        elif rest[i] == "--entry" and i + 1 < len(rest):
            k, v = rest[i + 1].split("=", 1)
            entries[k] = v
            i += 2
        else:
            sys.exit("bad argument: " + rest[i])
    files = []
    for root, dirs, names in os.walk(src):
        dirs.sort()
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(root, n), src).replace(os.sep, "/")
            if rel != "pack.json":
                files.append(rel)
    for rel in entries.values():
        if rel not in files and not any(f.startswith(rel.rstrip("/") + "/") for f in files):
            sys.exit("entry not found in pack: " + rel)
    manifest = json.dumps({"id": pid, "version": version, "files": files, "entrypoints": entries}, indent=1).encode()
    os.makedirs(out, exist_ok=True)
    tarbuf = io.BytesIO()
    unpacked = len(manifest)
    with tarfile.open(fileobj=tarbuf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        ti = tarfile.TarInfo("pack.json")
        ti.size = len(manifest)
        ti.mode = 0o644
        t.addfile(ti, io.BytesIO(manifest))
        for rel in files:
            p = os.path.join(src, rel)
            ti = tarfile.TarInfo(rel)
            ti.size = os.path.getsize(p)
            ti.mode = 0o755 if os.access(p, os.X_OK) else 0o644
            unpacked += ti.size
            with open(p, "rb") as f:
                t.addfile(ti, f)
    blob = lzma.compress(tarbuf.getvalue(), format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32, preset=6)
    fname = pid + "-" + version + ".tar.xz"
    with open(os.path.join(out, fname), "wb") as f:
        f.write(blob)
    info = {"id": pid, "name": name, "version": version, "file": fname, "sha256": hashlib.sha256(blob).hexdigest(), "size": len(blob), "unpacked": unpacked, "requires": requires}
    with open(os.path.join(out, pid + ".info.json"), "w") as f:
        json.dump(info, f, indent=1)
    print(fname, len(blob), "bytes,", len(files), "files")


def body(out, base):
    packs = []
    for n in sorted(os.listdir(out)):
        if n.endswith(".info.json"):
            with open(os.path.join(out, n)) as f:
                i = json.load(f)
            packs.append({"id": i["id"], "name": i["name"], "version": i["version"], "url": base.rstrip("/") + "/" + i["file"], "sha256": i["sha256"], "size": i["size"], "unpacked": i["unpacked"], "requires": i["requires"]})
    doc = {"format": 1, "packs": packs}
    with open(os.path.join(out, "catalog.body.json"), "w") as f:
        f.write(canon(doc))
    print("catalog.body.json:", len(packs), "packs")


def catalog(out, keydir, base):
    body(out, base)
    with open(os.path.join(out, "catalog.body.json")) as f:
        doc = json.load(f)
    der = run(["openssl", "dgst", "-sha256", "-sign", os.path.join(keydir, "catalog_key.pem")], canon(doc).encode())
    doc["sig"] = b64(der)
    with open(os.path.join(out, "catalog.json"), "w") as f:
        f.write(canon(doc))


def main(a):
    if len(a) >= 2 and a[0] == "keygen":
        return keygen(a[1])
    if len(a) >= 6 and a[0] == "build":
        return build(a[1], a[2], a[3], a[4], a[5], a[6:])
    if len(a) == 4 and a[0] == "catalog":
        return catalog(a[1], a[2], a[3])
    print(USAGE)
    sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
