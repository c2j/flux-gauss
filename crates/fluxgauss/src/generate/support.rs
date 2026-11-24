//! On-demand PL/SQL runtime support classes (issue #125).
//!
//! Oracle/GaussDB scalar builtins that have no inline Java equivalent are
//! lowered to static calls on `fluxgauss.plsql.StrUtil` / `fluxgauss.plsql.Regexp`.
//! The package is fixed (independent of `base_package`) so generated call
//! sites never need an import; each class file is written only when the
//! analysed logic actually references it.

use std::path::Path;

use encoding_rs::Encoding;

use crate::types::PackageInfo;

/// Scan analysed procedures for support-class references and emit the
/// corresponding `src/main/java/fluxgauss/plsql/*.java` files (paths
/// relative to `output_dir`, matching the other `generated` entries).
/// Files whose marker is absent are removed so shrinking the source set
/// does not leave stale helpers behind. Returns written paths.
pub fn write_support_classes(
    output_dir: &Path,
    packages: &[PackageInfo],
    encoding: &'static Encoding,
) -> Result<Vec<String>, std::io::Error> {
    let mut text = String::new();
    for pkg in packages {
        for proc in &pkg.procedures {
            for line in &proc.java_logic_lines {
                text.push_str(line);
                text.push(' ');
            }
            for line in proc.local_var_defaults.values() {
                text.push_str(line);
                text.push(' ');
            }
        }
    }

    let dir = output_dir.join("src/main/java/fluxgauss/plsql");
    let test_dir = output_dir.join("src/test/java/fluxgauss/plsql");
    let mut written = Vec::new();
    let java_charset = java_charset_name(encoding);
    // (marker, main file, main source, test file, test source)
    let classes = [
        (
            "fluxgauss.plsql.StrUtil",
            "StrUtil.java",
            strutil_java(java_charset),
            "StrUtilTest.java",
            strutil_test_java(encoding),
        ),
        ("fluxgauss.plsql.Regexp", "Regexp.java", REGEXP_JAVA.to_string(), "RegexpTest.java", regexp_test_java()),
    ];
    for (marker, file_name, source, test_name, test_source) in classes {
        let path = dir.join(file_name);
        let test_path = test_dir.join(test_name);
        if !text.contains(marker) {
            // Source set no longer references this helper — drop the stale
            // files instead of leaving dead code in the generated project.
            let _ = std::fs::remove_file(&path);
            let _ = std::fs::remove_file(&test_path);
            continue;
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        if let Some(parent) = test_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let (cow, _, _) = encoding.encode(&source);
        std::fs::write(&path, &cow)?;
        let (tcow, _, _) = encoding.encode(&test_source);
        std::fs::write(&test_path, &tcow)?;
        written.push(format!("src/main/java/fluxgauss/plsql/{}", file_name));
        written.push(format!("src/test/java/fluxgauss/plsql/{}", test_name));
    }
    Ok(written)
}

/// Map the configured SQL encoding to a `java.nio.charset.Charset` name.
/// Byte-semantic builtins (SUBSTRB/INSTRB/LENGTHB) follow the database
/// `server_encoding`; the generated helper must use the same charset or
/// CJK byte counts diverge (UTF-8: 3 B/char, GBK: 2 B/char).
fn java_charset_name(encoding: &'static Encoding) -> &'static str {
    match encoding.name() {
        "UTF-8" => "UTF-8",
        "GBK" => "GBK",
        "gb18030" | "GB18030" => "GB18030",
        "Big5" | "big5" => "Big5",
        "windows-1252" => "windows-1252",
        "Shift_JIS" | "shift_jis" => "Shift_JIS",
        "EUC-JP" | "euc-jp" => "EUC-JP",
        "ISO-2022-JP" | "iso-2022-jp" => "ISO-2022-JP",
        "UTF-16BE" | "utf-16be" => "UTF-16BE",
        "UTF-16LE" | "utf-16le" => "UTF-16LE",
        "US-ASCII" | "us-ascii" => "US-ASCII",
        "ISO-8859-1" | "iso-8859-1" => "ISO-8859-1",
        "ISO-8859-15" | "iso-8859-15" => "ISO-8859-15",
        "KOI8-R" | "koi8-r" => "KOI8-R",
        "macintosh" => "MacRoman",
        "x-mac-cyrillic" => "x-MacCyrillic",
        _ => "UTF-8",
    }
}

/// JUnit5 behavior tests for StrUtil, emitted next to the helper so every
/// generated project that uses it gets value-level verification under
/// `mvn test` (issue #125 review R3 3.7' — text goldens cannot catch
/// semantic divergence). Assertions are DB-verified where noted; the
/// charset-dependent byteLength expectation is computed from the same
/// `encoding_rs` config the helper is generated with.
fn strutil_test_java(encoding: &'static Encoding) -> String {
    let (zh_bytes, _, _) = encoding.encode("中文");
    format!(
        r#"package fluxgauss.plsql;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;

/** Auto-generated behavior checks for StrUtil (DB-verified semantics, issue #125). */
class StrUtilTest {{

    @Test
    void concatTreatsNullAsEmptyString() {{
        // DB-verified: 'a' || NULL = 'a'; NULL only when both sides NULL.
        assertEquals("a", StrUtil.concat("a", null));
        assertEquals("b", StrUtil.concat(null, "b"));
        assertEquals("ab", StrUtil.concat("a", "b"));
        assertNull(StrUtil.concat(null, null));
        // DB-verified numeric→text: 0.5→".5", -0.5→"-.5", scale kept.
        assertEquals(".5abc", StrUtil.concat(0.5, "abc"));
        assertEquals("42abc", StrUtil.concat(42L, "abc"));
        assertEquals("1.50x", StrUtil.concat(new java.math.BigDecimal("1.50"), "x"));
        assertEquals("-.5x", StrUtil.concat(-0.5, "x"));
    }}

    @Test
    void concatFunctions() {{
        // DB-verified: CONCAT NULL→empty; CONCAT_WS skips NULL args,
        // NULL separator → NULL.
        assertEquals("x", StrUtil.concatAll(null, "x"));
        assertEquals("ab", StrUtil.concatAll("a", null, "b"));
        assertNull(StrUtil.concatAll(null, null));
        assertEquals("a,b", StrUtil.concatWs(",", null, "a", "b"));
        assertEquals("a,b", StrUtil.concatWs(",", "a", null, "b"));
        assertNull(StrUtil.concatWs(null, "a", "b"));
    }}

    @Test
    void byteFunctionsAscii() {{
        assertEquals(Long.valueOf(5L), StrUtil.byteLength("hello"));
        assertEquals("ell", StrUtil.byteSubstr("hello", 2, 3));
        assertEquals(Long.valueOf(3L), StrUtil.byteInstr("hello", "ll"));
        assertNull(StrUtil.byteLength(null));
        assertNull(StrUtil.byteSubstr(null, 1));
    }}

    @Test
    void byteLengthFollowsConfiguredCharset() {{
        // '中文' is {} bytes in the configured DB charset — LENGTHB must
        // match server_encoding, not a hard-coded UTF-8.
        assertEquals(Long.valueOf({}L), StrUtil.byteLength("中文"));
    }}

    @Test
    void lobAndTextHelpers() {{
        assertEquals("hello", StrUtil.toText("hello"));
        assertNull(StrUtil.toText(null));
        assertArrayEquals("ab".getBytes(java.nio.charset.Charset.forName("{}")), StrUtil.toBytes("ab"));
        assertEquals("ell", StrUtil.lobSubstr("hello", 3, 2));
        assertEquals(Long.valueOf(5L), StrUtil.lobLength("hello"));
        assertNull(StrUtil.lobLength(null));
    }}
}}
"#,
        zh_bytes.len(),
        zh_bytes.len(),
        java_charset_name(encoding),
    )
}

/// JUnit5 behavior tests for Regexp — every assertion mirrors a value
/// verified against openGauss 7.0.0-RC3 (issue #125 review R3 §2/§3).
fn regexp_test_java() -> String {
    r#"package fluxgauss.plsql;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;

/** Auto-generated behavior checks for Regexp (DB-verified semantics, issue #125). */
class RegexpTest {

    @Test
    void replaceArgumentShapes() {
        // src='a1b2c3', pat='[a-z]', repl='X' — all rows DB-verified.
        assertEquals("X1b2c3", Regexp.replace("a1b2c3", "[a-z]", "X"));              // 3-arg: first only
        assertEquals("X1b2c3", Regexp.replace("a1b2c3", "[a-z]", "X", 1, 1, null));   // (1,1)
        assertEquals("X1X2X3", Regexp.replace("a1b2c3", "[a-z]", "X", 1, 0, null));   // (1,0): all
        assertEquals("a1X2c3", Regexp.replace("a1b2c3", "[a-z]", "X", 2, 1, null));   // (2,1)
        assertEquals("a1X2X3", Regexp.replace("a1b2c3", "[a-z]", "X", 2, 0, null));   // (2,0): prefix kept
        assertEquals("a1b2c3", Regexp.replace("a1b2c3", "[a-z]", "X", 9, 1, null));   // out of range
        assertEquals("XXX12XXX34", Regexp.replace("AbC12dEf34", "[a-z]", "X", 1, 0, "i"));
        assertEquals("XbC12dEf34", Regexp.replace("AbC12dEf34", "[a-z]", "X", 1, 1, "i"));
    }

    @Test
    void replaceNullArgumentRules() {
        assertNull(Regexp.replace(null, "[a-z]", "X"));          // NULL src → NULL
        assertEquals("a1b2c3", Regexp.replace("a1b2c3", null, "X")); // NULL pattern → src unchanged
        assertEquals("ac", Regexp.replace("abc", "b", null));     // NULL replacement → delete match
        assertNull(Regexp.replace("a1b2c3", "[a-z]", "X", 1, null)); // NULL occurrence → NULL
        // DB-verified precedence: NULL occurrence beats NULL pattern;
        // NULL position → NULL only in the 5/6-arg forms (4-arg defaults).
        assertNull(Regexp.replace("abc", null, "X", 1, null));
        assertNull(Regexp.replace("abc", "b", "X", null, 1));
        assertEquals("abc", Regexp.replace("abc", null, "X", null));
    }

    @Test
    void strictNumericArguments() {
        // DB ERRORs on out-of-range / non-integer position & occurrence.
        assertThrows(IllegalArgumentException.class, () -> Regexp.substr("abc", "b", 0));
        assertThrows(IllegalArgumentException.class, () -> Regexp.substr("abc", "b", 1, 0));
        assertThrows(IllegalArgumentException.class, () -> Regexp.substr("abc", "b", "x"));
        assertThrows(IllegalArgumentException.class, () -> Regexp.instr("abc", "b", 1, 1, -1));
        assertThrows(IllegalArgumentException.class, () -> Regexp.count("abc", "b", 0));
        assertThrows(IllegalArgumentException.class, () -> Regexp.replace("abc", "b", "X", 1, -1));
        // Explicit NULL numeric args → NULL result (not defaults).
        assertNull(Regexp.substr("abc", "b", null, 1));
        assertNull(Regexp.instr("abc", "b", null));
        assertNull(Regexp.count("abc", "b", null));
    }

    @Test
    void matchParameterLetters() {
        // regexp_like: only i/c/m accepted; other letters → INFO+false;
        // '' → ERROR (all DB-verified).
        assertTrue(Regexp.like("AbC", "[a-z]", "i"));
        assertFalse(Regexp.like("abc", "b", "g"));
        assertFalse(Regexp.like("abc", "b", "qs"));
        assertThrows(IllegalArgumentException.class, () -> Regexp.like("abc", "b", ""));
        // ARE family (substr/instr/count/replace): PG ARE letters
        // {b,c,e,i,m,n,p,q,s,t,w,x} + '' accepted; others → ERROR.
        assertEquals("aXc", Regexp.replace("abc", "b", "X", 1, 0, "x"));
        assertEquals("b", Regexp.substr("a1b2c3", "[a-z]+", 1, 2, "n"));
        assertThrows(IllegalArgumentException.class, () -> Regexp.replace("abc", "b", "X", 1, 0, "g"));
        assertThrows(IllegalArgumentException.class, () -> Regexp.replace("abc", "b", "X", 1, 0, "I"));
    }

    @Test
    void substrInstrCount() {
        assertEquals("b", Regexp.substr("a1b2c3", "[a-z]+", 1, 2, null));
        assertEquals(Long.valueOf(5L), Regexp.instr("a1b2c3", "[0-9]", 1, 2, 1, null));
        assertEquals(Long.valueOf(1L), Regexp.count("Aba", "a"));
        assertEquals(Long.valueOf(2L), Regexp.count("Aba", "a", 1, "i"));
    }
}
"#
    .to_string()
}

/// StrUtil source with the DB charset injected as a constant. Byte-window
/// decoding goes through a REPORT-mode decoder that shrinks the window
/// instead of emitting U+FFFD when SUBSTRB/INSTRB split a multi-byte char
/// (the DB backs off to the character boundary).
fn strutil_java(java_charset: &str) -> String {
    STRUTIL_JAVA
        .replace("import java.nio.charset.StandardCharsets;\n", "")
        .replace("StandardCharsets.UTF_8", "DB")
        .replace(
            "public final class StrUtil {\n    private StrUtil() {}",
            &format!(
                "public final class StrUtil {{\n    private static final java.nio.charset.Charset DB = java.nio.charset.Charset.forName(\"{}\");\n    private StrUtil() {{}}",
                java_charset
            ),
        )
}

const STRUTIL_JAVA: &str = r#"package fluxgauss.plsql;

import java.nio.charset.StandardCharsets;

/**
 * Oracle/GaussDB string & LOB scalar helpers lowered from PL/pgSQL builtins.
 *
 * Byte-semantic functions (SUBSTRB / INSTRB / LENGTHB) operate on UTF-8
 * bytes, matching the openGauss/GaussDB database encoding; LOB_* /
 * DBMS_LOB.* scalar reads use character semantics. All entry points
 * propagate SQL NULL as Java null.
 */
public final class StrUtil {
    private StrUtil() {}

    private static String str(Object o) {
        if (o == null) return null;
        if (o instanceof byte[] b) return new String(b, StandardCharsets.UTF_8);
        return String.valueOf(o);
    }

    private static byte[] bytes(Object o) {
        String s = str(o);
        return s == null ? null : s.getBytes(StandardCharsets.UTF_8);
    }

    private static long num(Object o, long dflt) {
        if (o == null) return dflt;
        if (o instanceof Number n) return n.longValue();
        try {
            return Long.parseLong(String.valueOf(o).trim());
        } catch (RuntimeException e) {
            return dflt;
        }
    }

    /** CLOB/BLOB → text; null propagates (Oracle NULL semantics). */
    public static String toText(Object o) {
        return str(o);
    }

    /** BLOB → raw bytes in the DB charset; byte[] passes through; null → null. */
    public static byte[] toBytes(Object o) {
        if (o instanceof byte[] b) return b;
        return bytes(o);
    }

    /**
     * Oracle/GaussDB `||`: a NULL operand acts as the empty string
     * (DB-verified: 'a' || NULL = 'a'), so the result is NULL only when
     * BOTH sides are NULL. Routing through a helper keeps each operand
     * single-evaluated (an inline ternary would evaluate it twice).
     * Numbers render with DB numeric-to-text rules: 0.5 -> ".5",
     * -0.5 -> "-.5", scale preserved (1.50 -> "1.50").
     */
    public static String concat(Object a, Object b) {
        if (a == null && b == null) return null;
        return cat(a) + cat(b);
    }

    /**
     * CONCAT(...) — DB-verified (openGauss 7.0.0-RC3 hz): NULL args act as
     * empty ('a',NULL,'b' → 'ab'), but ALL-NULL yields NULL
     * (concat(NULL,NULL) IS NULL → true), matching `||` both-null rule.
     */
    public static String concatAll(Object... args) {
        StringBuilder sb = new StringBuilder();
        boolean any = false;
        if (args != null) {
            for (Object a : args) {
                if (a != null) any = true;
                sb.append(cat(a));
            }
        }
        return any ? sb.toString() : null;
    }

    /**
     * CONCAT_WS(sep, a, b, ...) — DB-verified: NULL args are SKIPPED (no
     * separator emitted for them); a NULL separator yields NULL.
     */
    public static String concatWs(Object sep, Object... args) {
        String s = str(sep);
        if (s == null) return null;
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        if (args != null) {
            for (Object a : args) {
                if (a == null) continue;
                if (!first) sb.append(s);
                sb.append(cat(a));
                first = false;
            }
        }
        return sb.toString();
    }

    /** `||`/CONCAT operand → text; NULL → "". Numbers use DB formatting. */
    private static String cat(Object o) {
        if (o == null) return "";
        if (o instanceof Number n) return numText(n);
        String s = str(o);
        return s == null ? "" : s;
    }

    /** DB numeric→text: strip the leading zero of |x|<1, keep scale. */
    private static String numText(Number n) {
        String s;
        if (n instanceof java.math.BigDecimal bd) {
            s = bd.toPlainString();
        } else if (n instanceof Double || n instanceof Float) {
            double d = n.doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) return String.valueOf(n);
            s = java.math.BigDecimal.valueOf(d).toPlainString();
        } else {
            return n.toString();
        }
        if (s.startsWith("0.")) return s.substring(1);
        if (s.startsWith("-0.")) return "-" + s.substring(2);
        return s;
    }

    /** LENGTHB — UTF-8 byte length; null → null. */
    public static Long byteLength(Object o) {
        byte[] b = bytes(o);
        return b == null ? null : (long) b.length;
    }

    /** SUBSTRB(str, start[, len]) — byte window, 1-based / negative-from-end start. */
    public static String byteSubstr(Object s, Object start) {
        return byteSubstr(s, start, null);
    }

    public static String byteSubstr(Object s, Object start, Object len) {
        byte[] b = bytes(s);
        if (b == null) return null;
        long st = num(start, 1);
        int from;
        if (st > 0) {
            from = (int) Math.min(st - 1, Integer.MAX_VALUE);
        } else if (st < 0) {
            from = b.length + (int) st;
        } else {
            return null;
        }
        if (from < 0 || from >= b.length) return null;
        int lo;
        int hi;
        if (len == null) {
            lo = from;
            hi = b.length;
        } else {
            long l = num(len, 0);
            if (l >= 0) {
                lo = from;
                hi = (int) Math.min((long) b.length, from + Math.min(l, Integer.MAX_VALUE));
            } else {
                hi = from;
                lo = (int) Math.max(0L, from + Math.max(l, Integer.MIN_VALUE + 1L));
            }
        }
        if (hi <= lo) return "";
        return decodeWindow(b, lo, hi);
    }

    /**
     * Decode a byte window without emitting U+FFFD for split characters:
     * shrink whichever edge the decoder chokes on and retry, matching the
     * DB behaviour of backing SUBSTRB off to a character boundary.
     */
    private static String decodeWindow(byte[] b, int lo, int hi) {
        int curLo = lo;
        int curHi = hi;
        for (int guard = 0; guard < 16 && curHi > curLo; guard++) {
            java.nio.charset.CharsetDecoder dec = DB.newDecoder()
                .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
                .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT);
            java.nio.ByteBuffer in = java.nio.ByteBuffer.wrap(b, curLo, curHi - curLo);
            java.nio.CharBuffer out = java.nio.CharBuffer.allocate(curHi - curLo + 1);
            // decode(..)/flush(..) return CoderResult rather than throwing;
            // an error result means the window splits a character.
            java.nio.charset.CoderResult cr = dec.decode(in, out, true);
            if (!cr.isError()) {
                cr = dec.flush(out);
            }
            if (!cr.isError()) {
                return out.flip().toString();
            }
            // Decoder stopped at the malformed boundary: wrap() offsets the
            // buffer, so position()==curLo means a leading partial char
            // (drop lo), anything further means a trailing one (drop hi).
            // Bounded loop; clean windows decode first try.
            if (in.position() <= curLo) {
                curLo++;
            } else {
                curHi--;
            }
        }
        return new String(b, lo, hi - lo, DB);
    }

    /** INSTRB(str, sub[, pos[, occ]]) — 1-based byte position of nth occurrence; 0 if absent. */
    public static Long byteInstr(Object s, Object sub) {
        return byteInstr(s, sub, null, null);
    }

    public static Long byteInstr(Object s, Object sub, Object pos) {
        return byteInstr(s, sub, pos, null);
    }

    public static Long byteInstr(Object s, Object sub, Object pos, Object occ) {
        byte[] b = bytes(s);
        byte[] n = bytes(sub);
        if (b == null || n == null) return null;
        if (n.length == 0) return null; // DB: INSTRB(x, '') is NULL, not 0
        long st = num(pos, 1);
        int from;
        if (st > 0) {
            from = (int) Math.min(st - 1, Integer.MAX_VALUE);
        } else if (st < 0) {
            from = Math.max(0, b.length + (int) st);
        } else {
            return null;
        }
        if (from >= b.length) return 0L;
        long nth = Math.max(1, num(occ, 1));
        int idx = indexOf(b, n, from);
        for (long k = 1; k < nth && idx >= 0; k++) {
            idx = indexOf(b, n, idx + 1);
        }
        return idx < 0 ? 0L : (long) (idx + 1);
    }

    private static int indexOf(byte[] hay, byte[] needle, int from) {
        outer:
        for (int i = from; i + needle.length <= hay.length; i++) {
            for (int j = 0; j < needle.length; j++) {
                if (hay[i + j] != needle[j]) continue outer;
            }
            return i;
        }
        return -1;
    }

    /** LOB_GET_LENGTH / DBMS_LOB length — character count; null → null. */
    public static Long lobLength(Object lob) {
        String s = str(lob);
        return s == null ? null : (long) s.length();
    }

    /** DBMS_LOB.SUBSTR / DBE_LOB.SUBSTR — (lob[, amount[, offset]]); 1-based char offset. */
    public static String lobSubstr(Object lob) {
        return lobSubstr(lob, null, null);
    }

    public static String lobSubstr(Object lob, Object amount) {
        return lobSubstr(lob, amount, null);
    }

    public static String lobSubstr(Object lob, Object amount, Object offset) {
        String s = str(lob);
        if (s == null) return null;
        long off = num(offset, 1);
        if (off < 1) return null;
        int start = (int) Math.min(off - 1, s.length());
        if (start >= s.length()) return null;
        long amt = Math.max(0, num(amount, s.length()));
        int end = (int) Math.min((long) s.length(), start + Math.min(amt, Integer.MAX_VALUE));
        return s.substring(start, end);
    }

    /** DBE_LOB.INSTR / PKG_UTIL.LOB_MATCH — (lob, pattern[, offset[, nth]]); 1-based position, 0 if absent. */
    public static Long lobInstr(Object lob, Object pattern) {
        return lobInstr(lob, pattern, null, null);
    }

    public static Long lobInstr(Object lob, Object pattern, Object offset) {
        return lobInstr(lob, pattern, offset, null);
    }

    public static Long lobInstr(Object lob, Object pattern, Object offset, Object nth) {
        String s = str(lob);
        String p = str(pattern);
        if (s == null || p == null) return null;
        if (p.isEmpty()) return null; // DB: empty pattern position is NULL
        long off = num(offset, 1);
        if (off < 1) return 0L;
        int from = (int) Math.min(off - 1, s.length());
        long n = Math.max(1, num(nth, 1));
        int idx = s.indexOf(p, from);
        for (long k = 1; k < n && idx >= 0; k++) {
            idx = s.indexOf(p, idx + 1);
        }
        return idx < 0 ? 0L : (long) (idx + 1);
    }

    /** ASCII — code point of first char; null/empty → null. */
    public static Long ascii(Object o) {
        String s = str(o);
        return s == null || s.isEmpty() ? null : (long) s.codePointAt(0);
    }

    /** ASCIISTR — non-ASCII chars escaped as \XXXX. */
    public static String asciiStr(Object o) {
        String s = str(o);
        if (s == null) return null;
        StringBuilder sb = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c > 127) {
                sb.append(String.format("\\%04X", (int) c));
            } else {
                sb.append(c);
            }
        }
        return sb.toString();
    }

    /** UNISTR — decode \XXXX unicode escapes (BMP). */
    public static String unistr(Object o) {
        String s = str(o);
        if (s == null) return null;
        StringBuilder sb = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\' && i + 4 < s.length() && isHex4(s, i + 1)) {
                sb.append((char) Integer.parseInt(s.substring(i + 1, i + 5), 16));
                i += 4;
            } else if (c == '\\' && i + 1 < s.length() && s.charAt(i + 1) == '\\') {
                sb.append('\\');
                i += 1;
            } else {
                sb.append(c);
            }
        }
        return sb.toString();
    }

    private static boolean isHex4(String s, int off) {
        for (int i = 0; i < 4; i++) {
            char c = s.charAt(off + i);
            boolean hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
            if (!hex) return false;
        }
        return true;
    }
}
"#;

const REGEXP_JAVA: &str = r#"package fluxgauss.plsql;

import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

/**
 * Oracle REGEXP_* family lowered from PL/pgSQL builtins (issue #125).
 * Oracle uses POSIX-style patterns; java.util.regex covers the common
 * subset used by migrated code. match_parameter letters: i=caseless,
 * m=multiline, n=dotall, x=ignore-whitespace, c=caseless-off.
 */
public final class Regexp {
    private Regexp() {}

    private static String str(Object o) {
        return o == null ? null : String.valueOf(o);
    }

    /**
     * Strict integer for position/occurrence/return_option args.
     * DB-verified (openGauss 7.0.0-RC3): non-integer input → ERROR
     * "invalid input syntax for integer", so we throw
     * IllegalArgumentException rather than silently defaulting; a
     * non-integral Number (e.g. 1.5) is likewise rejected.
     */
    private static long strictNum(Object o, String what) {
        if (o instanceof Number n) {
            if (n.doubleValue() != n.longValue()) {
                throw new IllegalArgumentException("invalid " + what + ": " + o);
            }
            return n.longValue();
        }
        String s = str(o);
        try {
            return Long.parseLong(s == null ? "" : s.trim());
        } catch (RuntimeException e) {
            throw new IllegalArgumentException("invalid " + what + ": " + s);
        }
    }

    /**
     * Match parameters for regexp_substr/instr/count/replace — the DB
     * forwards them to the PG ARE engine, so the accepted set is the ARE
     * flag letters {b,c,e,i,m,n,p,q,s,t,w,x} plus '' (all DB-verified on
     * openGauss 7.0.0-RC3). Mappable: i→caseless, s→dotall (. matches
     * newline), x→expanded; c/m/n are already Java defaults
     * (case-sensitive / newline-sensitive). b/e/p/q/t/w have no
     * java.util.regex equivalent and are accepted as approximated
     * no-ops. Anything else → IllegalArgumentException (the DB ERRORs).
     * Case-SENSITIVE: uppercase 'I' is rejected, matching the DB.
     */
    private static int flags(Object params) {
        int f = 0;
        String p = str(params);
        if (p == null) return f;
        for (char c : p.toCharArray()) {
            switch (c) {
                case 'i': f |= Pattern.CASE_INSENSITIVE; break;
                case 's': f |= Pattern.DOTALL; break;
                case 'x': f |= Pattern.COMMENTS; break;
                case 'b': case 'c': case 'e': case 'm':
                case 'n': case 'p': case 'q': case 't': case 'w': break;
                default:
                    throw new IllegalArgumentException("invalid regexp option: " + c);
            }
        }
        return f;
    }

    /**
     * regexp_like match_parameter — DB-verified: this PL/pgSQL wrapper
     * accepts ONLY {i,c,m} (i=caseless, c=caseless-off, m=multiline);
     * any other letter logs INFO "illegal argument" and the function
     * returns FALSE (not an error); '' raises ERROR. NULL → no flags.
     * Returns -1 for the INFO→false case.
     */
    private static int likeFlags(Object params) {
        String p = str(params);
        if (p == null) return 0;
        if (p.isEmpty()) {
            throw new IllegalArgumentException("regexp_like: empty match_parameter");
        }
        int f = 0;
        for (char c : p.toCharArray()) {
            switch (c) {
                case 'i': f |= Pattern.CASE_INSENSITIVE; break;
                case 'c': break;
                case 'm': f |= Pattern.MULTILINE; break;
                default: return -1;
            }
        }
        return f;
    }

    /** 1-based position already validated ≥1; clamps into [0,len]. */
    private static int regionStart(long pos, int len) {
        return (int) Math.min(pos - 1, len);
    }

    /** REGEXP_LIKE(source, pattern[, match_parameter]) — partial match.
     *  NULL args → DB NULL; primitive boolean cannot represent
     *  three-valued logic, so false is returned (tracked deviation). */
    public static boolean like(Object s, Object p) {
        return like(s, p, null);
    }

    public static boolean like(Object s, Object p, Object params) {
        String src = str(s);
        String pat = str(p);
        if (src == null || pat == null) return false;
        int fl = likeFlags(params);
        if (fl < 0) return false;
        try {
            return Pattern.compile(pat, fl).matcher(src).find();
        } catch (PatternSyntaxException e) {
            return false;
        }
    }

    /** REGEXP_SUBSTR — nth match text from position; null if absent.
     *  DB-verified NULL rules: explicit NULL position/occurrence → NULL
     *  (absent args are defaulted by the overloads, so they differ). */
    public static String substr(Object s, Object p) {
        return substr(s, p, 1L, 1L, null);
    }

    public static String substr(Object s, Object p, Object pos) {
        return substr(s, p, pos, 1L, null);
    }

    public static String substr(Object s, Object p, Object pos, Object occ) {
        return substr(s, p, pos, occ, null);
    }

    public static String substr(Object s, Object p, Object pos, Object occ, Object params) {
        String src = str(s);
        String pat = str(p);
        if (src == null || pat == null) return null;
        if (pos == null || occ == null) return null;
        long ps = strictNum(pos, "position");
        long os = strictNum(occ, "occurrence");
        if (ps < 1) {
            throw new IllegalArgumentException("argument '" + ps + "' is out of range");
        }
        if (os < 1) {
            throw new IllegalArgumentException("argument '" + os + "' is out of range");
        }
        int start = regionStart(ps, src.length());
        try {
            Matcher m = Pattern.compile(pat, flags(params)).matcher(src);
            m.region(start, src.length());
            for (long k = 0; k < os; k++) {
                if (!m.find()) return null;
            }
            return m.group();
        } catch (PatternSyntaxException | IndexOutOfBoundsException e) {
            return null;
        }
    }

    /** REGEXP_INSTR — 1-based position of nth match (return_option≠0 → after match); 0 if absent.
     *  DB-verified NULL rules: explicit NULL position/occurrence/return_option → NULL. */
    public static Long instr(Object s, Object p) {
        return instr(s, p, 1L, 1L, 0L, null);
    }

    public static Long instr(Object s, Object p, Object pos) {
        return instr(s, p, pos, 1L, 0L, null);
    }

    public static Long instr(Object s, Object p, Object pos, Object occ) {
        return instr(s, p, pos, occ, 0L, null);
    }

    public static Long instr(Object s, Object p, Object pos, Object occ, Object retOpt) {
        return instr(s, p, pos, occ, retOpt, null);
    }

    public static Long instr(Object s, Object p, Object pos, Object occ, Object retOpt, Object params) {
        String src = str(s);
        String pat = str(p);
        if (src == null || pat == null) return null;
        if (pos == null || occ == null || retOpt == null) return null;
        long ps = strictNum(pos, "position");
        long os = strictNum(occ, "occurrence");
        long ro = strictNum(retOpt, "return_option");
        if (ps < 1 || os < 1 || ro < 0) {
            throw new IllegalArgumentException("argument out of range");
        }
        int start = regionStart(ps, src.length());
        try {
            Matcher m = Pattern.compile(pat, flags(params)).matcher(src);
            m.region(start, src.length());
            for (long k = 0; k < os; k++) {
                if (!m.find()) return 0L;
            }
            int at = ro != 0 ? m.end() : m.start();
            return (long) (at + 1);
        } catch (PatternSyntaxException | IndexOutOfBoundsException e) {
            return 0L;
        }
    }

    /**
     * REGEXP_COUNT(string, pattern[, position[, flags]]) — openGauss/O-mode
     * signature: the 4th arg is ARE match flags, NOT an occurrence
     * (DB-verified: regexp_count('Aba','a',1,'i') = 2). Explicit NULL
     * position → NULL; NULL flags → no flags.
     */
    public static Long count(Object s, Object p) {
        return count(s, p, 1L, null);
    }

    public static Long count(Object s, Object p, Object pos) {
        return count(s, p, pos, null);
    }

    public static Long count(Object s, Object p, Object pos, Object params) {
        String src = str(s);
        String pat = str(p);
        if (src == null || pat == null) return null;
        if (pos == null) return null;
        long ps = strictNum(pos, "position");
        if (ps < 1) {
            throw new IllegalArgumentException("argument '" + ps + "' is out of range");
        }
        int start = regionStart(ps, src.length());
        try {
            Matcher m = Pattern.compile(pat, flags(params)).matcher(src);
            m.region(start, src.length());
            int n = 0;
            while (m.find()) {
                n++;
            }
            return (long) n;
        } catch (PatternSyntaxException e) {
            return null;
        }
    }

    /**
     * REGEXP_REPLACE — openGauss O-mode argument semantics (all rows
     * DB-verified on openGauss 7.0.0-RC3):
     *   3-arg            → replace FIRST match only;
     *   4-arg (pos)      → replace ALL from position; position=NULL falls
     *                      back to default (unlike 5/6-arg!);
     *   occ=0            → all matches from position; occ=N>0 → Nth only;
     *   NULL src/occ     → NULL; NULL pos (5/6-arg) → NULL;
     *   NULL pattern     → source unchanged (but loses to NULL occ/pos);
     *   NULL replacement → delete matches;
     *   invalid position/occurrence → IllegalArgumentException (DB ERROR).
     * Oracle \N backrefs.
     */
    public static String replace(Object s, Object p, Object r) {
        return replace(s, p, r, 1L, 1L, null);
    }

    public static String replace(Object s, Object p, Object r, Object pos) {
        return replace(s, p, r, pos == null ? 1L : pos, 0L, null);
    }

    public static String replace(Object s, Object p, Object r, Object pos, Object occ) {
        return replace(s, p, r, pos, occ, null);
    }

    public static String replace(Object s, Object p, Object r, Object pos, Object occ, Object params) {
        String src = str(s);
        if (src == null) return null;
        // occ==null → explicit SQL NULL occurrence → NULL. Checked BEFORE
        // pattern: DB returns NULL even when the pattern is also NULL.
        if (occ == null) return null;
        // pos==null → NULL for the 5/6-arg forms (the 4-arg overload
        // substitutes 1L before reaching here).
        if (pos == null) return null;
        String pat = str(p);
        if (pat == null) return src;
        long ps = strictNum(pos, "position");
        long os = strictNum(occ, "occurrence");
        if (ps < 1 || os < 0) {
            throw new IllegalArgumentException("argument out of range");
        }
        int start = regionStart(ps, src.length());
        String repl = str(r);
        try {
            Matcher m = Pattern.compile(pat, flags(params)).matcher(src);
            m.region(start, src.length());
            // occurrence: 0 → ALL matches in region; N>0 → Nth only.
            if (os == 0) {
                StringBuilder sb = new StringBuilder(src.length());
                int last = 0; // keep the prefix before `start` — matches begin at `start`
                while (m.find()) {
                    sb.append(src, last, m.start())
                      .append(repl == null ? "" : expand(repl, m));
                    last = m.end();
                }
                sb.append(src.substring(last));
                return sb.toString();
            }
            long seen = 0;
            while (m.find()) {
                seen++;
                if (seen == os) {
                    return src.substring(0, m.start())
                        + (repl == null ? "" : expand(repl, m))
                        + src.substring(m.end());
                }
            }
            return src;
        } catch (PatternSyntaxException | IndexOutOfBoundsException e) {
            return null;
        }
    }

    /** Oracle replacement: \1..\9 group refs, \\ literal backslash. */
    private static String expand(String repl, Matcher m) {
        StringBuilder sb = new StringBuilder(repl.length());
        for (int i = 0; i < repl.length(); i++) {
            char c = repl.charAt(i);
            if (c == '\\' && i + 1 < repl.length()) {
                char d = repl.charAt(i + 1);
                if (d >= '1' && d <= '9') {
                    try {
                        // Unmatched optional groups yield null — Oracle
                        // substitutes the empty string, not "null".
                        String g = m.group(d - '0');
                        if (g != null) sb.append(g);
                    } catch (IndexOutOfBoundsException e) {
                        sb.append('\\').append(d);
                    }
                    i++;
                    continue;
                }
                if (d == '\\') {
                    sb.append('\\');
                    i++;
                    continue;
                }
            }
            sb.append(c);
        }
        return sb.toString();
    }
}
"#;
