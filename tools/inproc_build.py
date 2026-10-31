#!/usr/bin/env python3
"""Builds the in-process engines (d8, r8, ecj as dex) for the core pack and proves they work.

For each engine: javac the Entry wrapper(s) against the tool jar, dex the tool jar plus the wrapper
with d8 into dex/<name>.jar, then run the wrapper on the build machine's JVM (version check and a real
Java compile and dex of a tiny class). Any failure raises with the tool output, so a pack with a
dead engine is never published. The desktop JVM is not ART: this proves the wrapper logic and API
use, not the speed or memory on a phone.
"""
import os, shutil, subprocess, sys, tempfile, zipfile

PKG = "dev/makeroffline/tool"
HELLO = "public class Hello { public static void main(String[] a) { System.out.println(\"hi\"); } }\n"


def sh(cmd, run=subprocess.run, cwd=None):
    r = run(cmd, capture_output=True, cwd=cwd)
    out = (r.stdout or b"").decode(errors="replace") + (r.stderr or b"").decode(errors="replace")
    return r.returncode, out.strip()


def need(code, out, what):
    if code != 0:
        shown = out if len(out) <= 1300 else out[:500] + "\n...\n" + out[-700:]
        raise RuntimeError(what + " failed (exit " + str(code) + "): " + shown)


def jar_dir(d, out):
    """D8 takes .class, .jar, .zip and .dex files, not directories: pack a class directory into a jar first."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(d):
            dirs.sort()
            for f in sorted(files):
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, d).replace(os.sep, "/"))
    return out


def dex_jar(work, jar, classes_dir, out_jar, run):
    r8 = os.path.join(work, "r8", "r8.jar")
    lib = os.path.join(work, "android.jar")
    os.makedirs(os.path.dirname(out_jar), exist_ok=True)
    classes_jar = jar_dir(classes_dir, classes_dir.rstrip("/\\") + ".jar")
    code, out = sh(["java", "-cp", r8, "com.android.tools.r8.D8", "--release", "--min-api", "26", "--lib", lib, "--output", out_jar, jar, classes_jar], run)
    need(code, out, "dexing " + os.path.basename(jar))
    if not os.path.exists(out_jar):
        raise RuntimeError("d8 produced no " + out_jar)


def copy_resources(tool_jar, out_jar):
    """D8 writes classes.dex only. The tools read bundles and tables from their own jar (ecj: messages.properties,
    parser*.rsc), so every non-class file is copied into the dex jar; DexClassLoader serves them to getResource."""
    skip_sig = (".SF", ".RSA", ".DSA", ".EC")
    n = 0
    with zipfile.ZipFile(tool_jar) as src, zipfile.ZipFile(out_jar, "a", zipfile.ZIP_DEFLATED) as dst:
        have = set(dst.namelist())
        for info in src.infolist():
            name = info.filename
            if name.endswith("/") or name.endswith(".class") or name in have:
                continue
            if name.startswith("META-INF/versions/") or name == "META-INF/MANIFEST.MF":
                continue
            if name.startswith("META-INF/") and name.endswith(skip_sig):
                continue
            dst.writestr(name, src.read(name))
            have.add(name)
            n += 1
    return n


def require_resources(out_jar, names):
    with zipfile.ZipFile(out_jar) as z:
        have = set(z.namelist())
    missing = [n for n in names if n not in have]
    if missing:
        raise RuntimeError(out_jar + " lacks the resources " + ", ".join(missing) + " the tool reads at run time")


def dex_has_classes(out_jar, names):
    found = set()
    with zipfile.ZipFile(out_jar) as z:
        dexes = [n for n in z.namelist() if n.endswith(".dex")]
        if not dexes:
            raise RuntimeError(out_jar + " has no classes.dex")
        for n in dexes:
            blob = z.read(n)
            for c in names:
                if ("L" + PKG + "/" + c + ";").encode() in blob:
                    found.add(c)
    missing = [c for c in names if c not in found]
    if missing:
        raise RuntimeError(out_jar + " does not contain " + ", ".join(missing))


def smoke(work, spec, cls, run):
    jar = os.path.join(work, spec["jar"])
    cp = os.pathsep.join([jar, cls])
    lib = os.path.join(work, "android.jar")
    tmp = tempfile.mkdtemp()
    try:
        if spec["check"] == "d8":
            code, out = sh(["java", "-cp", cp, "dev.makeroffline.tool.D8Entry", "--version"], run)
            need(code, out, "D8Entry --version")
            if not out.startswith("D8"):
                raise RuntimeError("D8Entry --version printed " + out[:100])
            code, out = sh(["java", "-cp", cp, "dev.makeroffline.tool.R8Entry", "--version"], run)
            need(code, out, "R8Entry --version")
            if not out.startswith("R8"):
                raise RuntimeError("R8Entry --version printed " + out[:100])
        else:
            code, out = sh(["java", "-cp", cp, "dev.makeroffline.tool.EcjEntry", "-version"], run)
            need(code, out, "EcjEntry -version")
            src = os.path.join(tmp, "Hello.java")
            with open(src, "w") as f:
                f.write(HELLO)
            outc = os.path.join(tmp, "classes")
            os.makedirs(outc)
            code, out = sh(["java", "-cp", cp, "dev.makeroffline.tool.EcjEntry", "-source", "8", "-target", "8", "-nowarn", "-bootclasspath", lib, "-d", outc, src], run)
            need(code, out, "EcjEntry compile")
            if not os.path.exists(os.path.join(outc, "Hello.class")):
                raise RuntimeError("EcjEntry compiled without producing Hello.class")
            r8jar = os.path.join(work, "r8", "r8.jar")
            dexout = os.path.join(tmp, "dexout")
            os.makedirs(dexout)
            code, out = sh(["java", "-cp", os.pathsep.join([r8jar, os.path.join(work, ".inproc", "r8cls")]), "dev.makeroffline.tool.D8Entry", "--release", "--min-api", "26", "--lib", lib, "--output", dexout, jar_dir(outc, outc + ".jar")], run)
            need(code, out, "D8Entry dex of the compiled class")
            if not os.path.exists(os.path.join(dexout, "classes.dex")):
                raise RuntimeError("D8Entry produced no classes.dex")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build(work, specs, srcdir, report, run=subprocess.run):
    inproc_dir = os.path.join(srcdir, "inproc")
    if not os.path.isdir(inproc_dir):
        inproc_dir = srcdir
    report["engines"] = {}
    for spec in specs:
        jar = os.path.join(work, spec["jar"])
        cls = os.path.join(work, ".inproc", spec["name"] + "cls")
        shutil.rmtree(cls, ignore_errors=True)
        os.makedirs(cls)
        srcs = [os.path.join(inproc_dir, s) for s in spec["sources"]]
        for s in srcs:
            if not os.path.exists(s):
                raise RuntimeError("missing wrapper source " + s)
        code, out = sh(["javac", "--release", "11", "-proc:none", "-nowarn", "-cp", jar, "-d", cls] + srcs, run)
        need(code, out, "javac of " + ", ".join(spec["sources"]))
        out_jar = os.path.join(work, spec["out"])
        dex_jar(work, jar, cls, out_jar, run)
        res = copy_resources(jar, out_jar)
        require_resources(out_jar, spec.get("resources", []))
        dex_has_classes(out_jar, spec["classes"])
        smoke(work, spec, cls, run)
        report["engines"][spec["name"]] = {"out": spec["out"], "bytes": os.path.getsize(out_jar), "resources": res, "smoke": "ok on desktop JVM"}
    shutil.rmtree(os.path.join(work, ".inproc"), ignore_errors=True)
