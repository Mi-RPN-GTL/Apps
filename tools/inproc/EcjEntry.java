package dev.makeroffline.tool;

import java.io.PrintStream;
import java.io.PrintWriter;
import org.eclipse.jdt.internal.compiler.batch.Main;

/** Runs the Eclipse batch compiler inside the app process, without System.exit. */
public final class EcjEntry {
    private EcjEntry() {
    }

    public static int run(String[] args, PrintStream out, PrintStream err) {
        PrintWriter o = new PrintWriter(out, true);
        PrintWriter e = new PrintWriter(err, true);
        try {
            Main m = new Main(o, e, false);
            boolean ok = m.compile(args);
            o.flush();
            e.flush();
            return ok ? 0 : 1;
        } catch (Throwable t) {
            e.println(t.getClass().getSimpleName() + ": " + t.getMessage());
            e.flush();
            return 2;
        }
    }

    /** Only for the build checks on a desktop JVM. */
    public static void main(String[] args) {
        System.exit(run(args, System.out, System.err));
    }
}
