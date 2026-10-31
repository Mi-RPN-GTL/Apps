package dev.makeroffline.tool;

import com.android.tools.r8.CompilationFailedException;
import com.android.tools.r8.D8;
import com.android.tools.r8.D8Command;
import com.android.tools.r8.Diagnostic;
import com.android.tools.r8.DiagnosticsHandler;
import com.android.tools.r8.origin.Origin;
import java.io.PrintStream;

/** Runs D8 inside the app process. Never calls System.exit (that would kill the app). */
public final class D8Entry {
    private D8Entry() {
    }

    static final class Handler implements DiagnosticsHandler {
        final PrintStream out;
        final PrintStream err;
        int errors;

        Handler(PrintStream out, PrintStream err) {
            this.out = out;
            this.err = err;
        }

        @Override
        public void error(Diagnostic d) {
            errors++;
            err.println("error: " + d.getDiagnosticMessage());
        }

        @Override
        public void warning(Diagnostic d) {
            err.println("warning: " + d.getDiagnosticMessage());
        }

        @Override
        public void info(Diagnostic d) {
            out.println(d.getDiagnosticMessage());
        }
    }

    private static String version() {
        try {
            return String.valueOf(Class.forName("com.android.tools.r8.Version").getMethod("getVersionString").invoke(null));
        } catch (Throwable t) {
            return "unknown";
        }
    }

    public static int run(String[] args, PrintStream out, PrintStream err) {
        for (String a : args) {
            if (a.equals("--version")) {
                out.println("D8 " + version());
                return 0;
            }
            if (a.equals("--help")) {
                out.println("Usage: D8 [options] <input files>. Options are those of the command line tool.");
                return 0;
            }
        }
        Handler h = new Handler(out, err);
        try {
            D8Command cmd = D8Command.parse(args, Origin.root(), h).build();
            D8.run(cmd);
            return h.errors > 0 ? 1 : 0;
        } catch (CompilationFailedException e) {
            return 1;
        } catch (OutOfMemoryError | LinkageError t) {
            err.println("engine unusable: " + t.getClass().getSimpleName() + ": " + t.getMessage());
            return 125;
        } catch (Throwable t) {
            err.println(t.getClass().getSimpleName() + ": " + t.getMessage());
            return 2;
        }
    }

    /** Only for the build checks on a desktop JVM. */
    public static void main(String[] args) {
        System.exit(run(args, System.out, System.err));
    }
}
