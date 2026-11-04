import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;

/** A plausible port that uses binary floating point: wrong on exact halves. */
public class Interest {
    public static void main(String[] args) throws IOException {
        if (args.length != 1) {
            System.err.println("usage: Interest FILE");
            System.exit(64);
        }
        List<String> lines = Files.readAllLines(Path.of(args[0]), StandardCharsets.UTF_8);
        StringBuilder out = new StringBuilder();
        for (String line : lines) {
            if (line.isBlank()) {
                continue;
            }
            String[] field = line.split(",");
            double interest = Math.round(Double.parseDouble(field[1]) * Double.parseDouble(field[2]) * 100) / 100.0;
            out.append(field[0]).append(',').append(String.format(Locale.ROOT, "%.2f", interest)).append('\n');
        }
        System.out.print(out);
    }
}
