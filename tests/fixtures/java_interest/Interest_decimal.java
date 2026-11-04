import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** Interest per account: amount x rate, rounded half away from zero to cents (COBOL ROUNDED). */
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
            BigDecimal interest = new BigDecimal(field[1]).multiply(new BigDecimal(field[2]))
                    .setScale(2, RoundingMode.HALF_UP);
            out.append(field[0]).append(',').append(interest.toPlainString()).append('\n');
        }
        System.out.print(out);
    }
}
