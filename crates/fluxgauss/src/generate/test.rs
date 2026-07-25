use std::collections::BTreeSet;
use std::path::Path;

use encoding_rs::Encoding;

use crate::generate::mapper::is_simple_java_type;
use crate::generate::writer::CodeWriter;
use crate::naming::{java_method_name, package_to_classname, snake_to_camel};
use crate::types::{DmlType, PackageInfo};

static IDENTIFIER_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();

fn case_insensitive_word_match(text: &str, word: &str) -> bool {
    let lower_text = text.to_lowercase();
    let lower_word = word.to_lowercase();
    let mut start = 0;
    while let Some(pos) = lower_text[start..].find(&lower_word) {
        let abs_pos = start + pos;
        let before_ok = abs_pos == 0 || !lower_text.as_bytes()[abs_pos - 1].is_ascii_alphanumeric()
            && lower_text.as_bytes()[abs_pos - 1] != b'_';
        let after_pos = abs_pos + lower_word.len();
        let after_ok = after_pos >= lower_text.len()
            || (!lower_text.as_bytes()[after_pos].is_ascii_alphanumeric()
                && lower_text.as_bytes()[after_pos] != b'_');
        if before_ok && after_ok {
            return true;
        }
        start = abs_pos + 1;
    }
    false
}

pub fn write_service_test(
    base_path: &Path,
    pkg: &PackageInfo,
    base_package: &str,
    service_injections: &std::collections::HashMap<String, String>,
    encoding: &'static Encoding,
) -> std::io::Result<String> {
    let java_pkg = format!("{}.service", base_package);
    let test_dir = base_path.join(format!("src/test/java/{}/service", base_package.replace('.', "/")));
    let class_name = format!("{}Service", package_to_classname(&pkg.package_name));
    let mapper_var = lowercase_first(&package_to_classname(&pkg.package_name)) + "Mapper";
    let test_class_name = format!("{}Test", class_name);

    let mut imports: BTreeSet<String> = BTreeSet::new();
    imports.insert("import org.junit.jupiter.api.Test;".to_string());
    imports.insert("import org.junit.jupiter.api.extension.ExtendWith;".to_string());
    imports.insert("import org.mockito.InjectMocks;".to_string());
    imports.insert("import org.mockito.Mock;".to_string());
    imports.insert("import org.mockito.junit.jupiter.MockitoExtension;".to_string());
    imports.insert("import org.mockito.junit.jupiter.MockitoSettings;".to_string());
    imports.insert("import org.mockito.quality.Strictness;".to_string());
    imports.insert(format!("import {}.{};", java_pkg, class_name));
    imports.insert(format!(
        "import {}.mapper.{}Mapper;",
        base_package,
        package_to_classname(&pkg.package_name)
    ));
    imports.insert(format!("import {}.exception.BusinessException;", base_package));
    imports.insert("import static org.mockito.Mockito.*;".to_string());
    imports.insert("import static org.junit.jupiter.api.Assertions.*;".to_string());

    for (svc_var, pkg_name) in service_injections {
        let svc_class = if !pkg_name.is_empty() {
            format!("{}Service", package_to_classname(pkg_name))
        } else {
            let part = svc_var.replace("Service", "");
            format!("{}Service", package_to_classname(&part))
        };
        imports.insert(format!("import {}.service.{};", base_package, svc_class));
    }

    if pkg.procedures.iter().any(|p| p.parameters.iter().any(|p| p.is_out())) {
        imports.insert("import java.util.concurrent.atomic.AtomicReference;".to_string());
    }

    if pkg.procedures.iter().any(|p| p.parameters.iter().any(|p| p.java_type.contains("Map"))) {
        imports.insert("import java.util.Map;".to_string());
        imports.insert("import java.util.HashMap;".to_string());
    }

    let mut w = CodeWriter::new();
    w.line(&format!("package {};", java_pkg));
    w.blank();
    for imp in &imports {
        w.line(imp);
    }
    w.blank();
    w.line("@ExtendWith(MockitoExtension.class)");
    w.line("@MockitoSettings(strictness = Strictness.LENIENT)");
    if !pkg.source_file.is_empty() {
        w.line(&format!("// Source: {}", pkg.source_file));
    }
    w.line(&format!("class {} {{", test_class_name));
    w.blank();
    w.push_indent();

    w.line("@Mock");
    w.line(&format!(
        "private {} {};",
        format!("{}Mapper", package_to_classname(&pkg.package_name)),
        mapper_var
    ));

    for (svc_var, pkg_name) in service_injections {
        let svc_class = if !pkg_name.is_empty() {
            format!("{}Service", package_to_classname(pkg_name))
        } else {
            let part = svc_var.replace("Service", "");
            format!("{}Service", package_to_classname(&part))
        };
        w.blank();
        w.line("@Mock");
        w.line(&format!("private {} {};", svc_class, svc_var));
    }

    w.blank();
    w.line("@InjectMocks");
    w.line(&format!("private {} service;", class_name));

    let overload_counts: std::collections::HashMap<String, usize> = pkg.procedures.iter()
        .map(|p| java_method_name(&p.proc_name))
        .fold(std::collections::HashMap::new(), |mut m, name| {
            *m.entry(name).or_insert(0) += 1;
            m
        });
    let mut overload_idx: std::collections::HashMap<String, usize> = std::collections::HashMap::new();

    for proc in &pkg.procedures {
        w.blank();
        let java_name = java_method_name(&proc.proc_name);
        let is_overloaded = overload_counts.get(&java_name).copied().unwrap_or(1) > 1;
        let suffix = if is_overloaded {
            let idx = overload_idx.entry(java_name.clone()).or_insert(0);
            *idx += 1;
            format!("_{}", idx)
        } else {
            String::new()
        };
        let test_method = build_success_test(proc, &mapper_var, pkg, &suffix);
        for line in test_method.split('\n') {
            w.line(line);
        }
    }

    w.pop_indent();
    w.line("}");

    std::fs::create_dir_all(&test_dir)?;
    let file_path = test_dir.join(format!("{}.java", test_class_name));
    w.write_to_file(&file_path, encoding)?;
    Ok(test_class_name)
}

fn count_mapper_params_for_dml(
    proc: &crate::types::ProcedureInfo,
    dml: &crate::types::DmlStatement,
    pkg: &PackageInfo,
) -> usize {
    let in_count = proc.parameters.iter().filter(|p| !p.is_out()).count();
    // Collect all param java names (lowercase) — mirroring mapper.rs logic:
    //   1. non-out proc params + 2. extra_params + 3. local var refs + 4. pkg var refs + 5. OUT params
    let mut all_names: std::collections::HashSet<String> = proc
        .parameters.iter()
        .filter(|p| !p.is_out())
        .map(|p| snake_to_camel(&p.name).to_lowercase())
        .collect();

    // Step 2: dml.extra_params (deduped against proc params)
    for (jn, _) in &dml.extra_params {
        all_names.insert(jn.to_lowercase());
    }

    // Step 3: local vars referenced in SQL text
    let local_var_refs = count_local_var_refs_in_sql(&dml.sql_text, &proc.local_vars, &proc.parameters);
    // count_local_var_refs_in_sql already returns count of unique local vars not in proc.params
    // We need their actual names to prevent double-counting with extra_params.
    // Re-implement inline to get names:
    let param_names: std::collections::HashSet<String> = proc
        .parameters.iter()
        .filter(|p| !p.is_out())
        .map(|p| p.name.to_lowercase())
        .collect();
    let re = IDENTIFIER_RE.get_or_init(|| regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap());
    for caps in re.captures_iter(&dml.sql_text) {
        let word = caps.get(1).unwrap().as_str();
        if proc.local_vars.contains_key(&word.to_lowercase()) && !param_names.contains(&word.to_lowercase()) {
            let jn = snake_to_camel(word).to_lowercase();
            all_names.insert(jn);
        }
    }

    // Step 4: package vars referenced in SQL text
    let pkg_param_names: std::collections::HashSet<String> = proc
        .parameters.iter()
        .filter(|p| !p.is_out())
        .map(|p| p.name.to_lowercase())
        .collect();
    for caps in re.captures_iter(&dml.sql_text) {
        let word = caps.get(1).unwrap().as_str();
        if pkg.package_vars.contains_key(word)
            && !proc.local_vars.contains_key(&word.to_lowercase())
            && !pkg_param_names.contains(&word.to_lowercase())
        {
            let jn = snake_to_camel(word).to_lowercase();
            all_names.insert(jn);
        }
    }

    // Step 5: OUT params in SQL text (already tracked in all_names via step 1 if they exist)
    // Add OUT params found in SQL text
    for p in &proc.parameters {
        if p.is_out() {
            if case_insensitive_word_match(&dml.sql_text, &p.name) {
                let jn = snake_to_camel(&p.name).to_lowercase();
                all_names.insert(jn);
            }
        }
    }

    all_names.len()
}

fn build_success_test(
    proc: &crate::types::ProcedureInfo,
    mapper_name: &str,
    pkg: &PackageInfo,
    overload_suffix: &str,
) -> String {
    let method_name = java_method_name(&proc.proc_name);
    let mut lines = Vec::new();

    let needs_disable = proc_has_unterminated_loop(proc, pkg);

    if needs_disable {
        lines.push("    @org.junit.jupiter.api.Disabled(\"auto-generated mock cannot terminate while loop\")".to_string());
    }
    lines.push("    @Test".to_string());
    lines.push("    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)".to_string());
    lines.push(format!("    void test_{}_success{}() {{", method_name, overload_suffix));

    let mut param_values: Vec<String> = Vec::new();
    let mut param_args: Vec<String> = Vec::new();

    for p in &proc.parameters {
        if p.is_out() {
            if p.is_refcursor() {
                continue;
            }
            let inner_type = p.java_type.clone();
            let ref_var = format!("{}Ref", snake_to_camel(&p.name));
            param_values.push(format!(
                "AtomicReference<{}> {} = new AtomicReference<>(null);",
                inner_type, ref_var
            ));
            param_args.push(ref_var);
        } else {
            let val = default_test_value(&p.java_type, &snake_to_camel(&p.name));
            param_values.push(format!("{} {} = {};", p.java_type, snake_to_camel(&p.name), val));
            param_args.push(snake_to_camel(&p.name));
        }
    }

    for pv in &param_values {
        lines.push(format!("        {}", pv));
    }

    let all_mock_lines = mock_all_mapper_methods(mapper_name, pkg);
    for ml in &all_mock_lines {
        lines.push(ml.clone());
    }

    let args_str = param_args.join(", ");
    let has_refcursor_out = proc.parameters.iter().any(|p| p.is_out() && p.is_refcursor());
    if proc.is_function || has_refcursor_out {
        lines.push(format!("        var result = service.{}({});", method_name, args_str));
        if has_refcursor_out && !proc.open_cursors.is_empty() {
            lines.push("        assertNotNull(result);".to_string());
        }
    } else {
        lines.push(format!("        service.{}({});", method_name, args_str));
    }

    let first_dml = proc.dml_statements.iter()
        .find(|d| matches!(d.sql_type, DmlType::Insert | DmlType::Update | DmlType::Delete));
    if let Some(dml) = first_dml {
        let any_count = count_mapper_params_for_dml(proc, dml, pkg);
        let mut typed_args = Vec::new();
        for p in proc.parameters.iter().filter(|pp| !pp.is_out()) {
            typed_args.push(format!("({}) any()", any_matcher_type(&p.java_type)));
        }
        for (_, jt) in &dml.extra_params {
            typed_args.push(format!("({}) any()", any_matcher_type(jt)));
        }
        let verify_is_overloaded = pkg.procedures.iter()
            .flat_map(|pp| pp.dml_statements.iter())
            .filter(|dd| dd.method_id == dml.method_id)
            .count() > 1;
        let verify_args = if verify_is_overloaded && !typed_args.is_empty() {
            while typed_args.len() < any_count { typed_args.push("any()".to_string()); }
            typed_args.join(", ")
        } else if any_count > 0 {
            vec!["any()"; any_count].join(", ")
        } else {
            String::new()
        };
        lines.push(format!("        verify({}, atLeast(0)).{}({});", mapper_name, dml.method_id, verify_args));
    }

    lines.push("    }".to_string());
    lines.join("\n")
}

fn proc_has_unterminated_loop(proc: &crate::types::ProcedureInfo, pkg: &PackageInfo) -> bool {
    let has_while_true = proc.java_logic_lines.iter().any(|l| {
        let t = l.trim();
        t.contains("while (true)") || t.contains("while(true)")
    });
    if has_while_true {
        return true;
    }
    let has_recursive_call = proc.service_calls.iter().any(|sc| {
        sc.package_name == pkg.package_name && sc.method_name == java_method_name(&proc.proc_name)
    });
    if has_recursive_call {
        return true;
    }
    let camel_name = java_method_name(&proc.proc_name);
    let has_self_call_in_expr = proc.java_logic_lines.iter().any(|l| {
        l.contains(&format!("this.{}(", camel_name))
    });
    if has_self_call_in_expr {
        return true;
    }
    let has_while_with_mapper = {
        let mut in_while = false;
        for l in &proc.java_logic_lines {
            let t = l.trim();
            if t.starts_with("while ") || t.starts_with("while(") {
                in_while = true;
            }
            if in_while && t.contains("mapper.") {
                return true;
            }
            if t == "}" {
                in_while = false;
            }
        }
        false
    };
    has_while_with_mapper
}

fn count_local_var_refs_in_sql(
    sql_text: &str,
    local_vars: &std::collections::HashMap<String, String>,
    params: &[crate::types::Parameter],
) -> usize {
    let param_names: std::collections::HashSet<String> = params
        .iter()
        .filter(|p| !p.is_out())
        .map(|p| p.name.to_lowercase())
        .collect();
    let mut found: std::collections::HashSet<String> = std::collections::HashSet::new();
    let re = IDENTIFIER_RE.get_or_init(|| regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap());
    for caps in re.captures_iter(sql_text) {
        let word = caps.get(1).unwrap().as_str();
        if local_vars.contains_key(&word.to_lowercase()) && !param_names.contains(&word.to_lowercase()) {
            found.insert(word.to_lowercase());
        }
    }
    found.len()
}

fn count_package_var_refs_in_sql(
    sql_text: &str,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
    local_vars: &std::collections::HashMap<String, String>,
    params: &[crate::types::Parameter],
) -> usize {
    let param_names: std::collections::HashSet<String> = params
        .iter()
        .filter(|p| !p.is_out())
        .map(|p| p.name.to_lowercase())
        .collect();
    let mut found: std::collections::HashSet<String> = std::collections::HashSet::new();
    let re = IDENTIFIER_RE.get_or_init(|| regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap());
    for caps in re.captures_iter(sql_text) {
        let word = caps.get(1).unwrap().as_str();
        if package_vars.contains_key(word)
            && !param_names.contains(&word.to_lowercase())
            && !local_vars.contains_key(&word.to_lowercase())
        {
            found.insert(word.to_lowercase());
        }
    }
    found.len()
}

fn extract_select_columns(sql: &str) -> Vec<String> {
    let upper = sql.to_uppercase();
    let select_pos = match upper.find("SELECT") {
        Some(p) => p,
        None => return Vec::new(),
    };
    let from_pos = match upper.find(" FROM ") {
        Some(p) => p,
        None => return Vec::new(),
    };
    if from_pos <= select_pos + 6 {
        return Vec::new();
    }
    let cols_part = &sql[select_pos + 6..from_pos];
    let mut cols = Vec::new();
    for part in cols_part.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let up = part.to_uppercase();
        if up == "*" {
            return Vec::new();
        }
        let part = if up.starts_with("DISTINCT ") {
            &part[9..]
        } else {
            part
        };
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let col_name = if let Some(as_pos) = part.to_uppercase().find(" AS ") {
            part[as_pos + 4..].trim()
        } else {
            part
        };
        let col_name = if let Some(dot_pos) = col_name.rfind('.') {
            &col_name[dot_pos + 1..]
        } else {
            col_name
        };
        let col_name = col_name.trim().trim_matches('"').trim_matches('\'');
        let col_name = if let Some(space_pos) = col_name.find(' ') {
            &col_name[..space_pos]
        } else {
            col_name
        };
        let col_name = col_name.replace('"', "");
        if !col_name.is_empty() && col_name != "*" && !col_name.contains('(') && !col_name.contains(')') {
            cols.push(col_name.to_lowercase());
        }
    }
    cols
}

fn column_mock_value(col_name: &str) -> String {
    column_mock_value_for_key(&crate::naming::snake_to_camel(col_name))
}

fn column_mock_value_for_key(camel_key: &str) -> String {
    let nl = camel_key.to_lowercase();
    if nl.ends_with("id") || nl.ends_with("no") || nl == "id" {
        return "1L".to_string();
    }
    if nl == "name"
        || nl.ends_with("name")
        || nl.ends_with("code")
        || nl.ends_with("type")
        || nl.ends_with("status")
        || nl.ends_with("desc")
        || nl.ends_with("text")
        || nl.ends_with("memo")
        || nl.ends_with("reason")
    {
        return "\"test\"".to_string();
    }
    if nl == "salary"
        || nl.ends_with("salary")
        || nl.ends_with("count")
        || nl.ends_with("qty")
        || nl.ends_with("num")
        || nl.ends_with("amount")
        || nl.ends_with("total")
        || nl.ends_with("price")
        || nl.ends_with("fee")
        || nl.ends_with("rate")
        || nl.ends_with("pct")
        || nl.ends_with("bonus")
        || nl.ends_with("balance")
    {
        return "java.math.BigDecimal.TEN".to_string();
    }
    if nl.ends_with("date") || nl.ends_with("time") {
        return "java.sql.Date.valueOf(\"2024-01-01\")".to_string();
    }
    if nl.ends_with("flag") || nl.starts_with("is") {
        return "true".to_string();
    }
    if nl == "age" || nl.ends_with("age") || nl == "year" || nl.ends_with("year") || nl == "month" || nl.ends_with("month") || nl == "day" || nl.ends_with("day") {
        return "1".to_string();
    }
    "1L".to_string()
}

fn build_map_mock(cols: &[String]) -> String {
    let mut s = "{ var m = new java.util.HashMap<String,Object>(); ".to_string();
    for col in cols {
        let val = column_mock_value(col);
        let camel_key = crate::naming::snake_to_camel(col);
        let safe_key = escape_java_string(&camel_key);
        s.push_str(&format!("m.put(\"{}\", {}); ", safe_key, val));
    }
    s
}

fn escape_java_string(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('"', "\\\"")
        .replace('\t', "\\t")
}

/// Scan all procedures in the package for `.get("key")` patterns in logic lines,
/// returning camelCase keys that the service code accesses on Map results.
fn extract_map_access_keys(pkg: &PackageInfo) -> Vec<String> {
    let mut keys = std::collections::HashSet::new();
    let re = regex::Regex::new(r#"\.get\("(\w+)"\)"#).unwrap_or_else(|_| regex::Regex::new(r#""#).unwrap());
    for proc in &pkg.procedures {
        for line in &proc.java_logic_lines {
            for cap in re.captures_iter(line) {
                if let Some(k) = cap.get(1) {
                    keys.insert(k.as_str().to_string());
                }
            }
        }
    }
    let mut result: Vec<String> = keys.into_iter().collect();
    result.sort();
    result
}

  fn mock_all_mapper_methods(mapper_name: &str, pkg: &PackageInfo) -> Vec<String> {
       let extra_keys = extract_map_access_keys(pkg);
       let mut all_dmls: Vec<(String, DmlType, usize, bool, Option<String>, String, Vec<String>)> = Vec::new();
       for proc in &pkg.procedures {
           for dml in &proc.dml_statements {
               let mut param_types: Vec<String> = proc.parameters.iter()
                   .filter(|p| !p.is_out())
                   .map(|p| p.java_type.clone())
                   .collect();
               for (_, jt) in &dml.extra_params {
                   param_types.push(jt.clone());
               }
               let in_param_count = proc.parameters.iter().filter(|p| !p.is_out()).count();
               let mut all_names: std::collections::HashSet<String> = proc
                   .parameters.iter()
                   .filter(|p| !p.is_out())
                   .map(|p| crate::naming::snake_to_camel(&p.name).to_lowercase())
                   .collect();

               for (jn, _) in &dml.extra_params { all_names.insert(jn.to_lowercase()); }

               let re = IDENTIFIER_RE.get_or_init(|| regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap());
               let proc_param_names: std::collections::HashSet<String> = proc
                   .parameters.iter().filter(|p| !p.is_out()).map(|p| p.name.to_lowercase()).collect();
               for caps in re.captures_iter(&dml.sql_text) {
                   let word = caps.get(1).unwrap().as_str();
                   if proc.local_vars.contains_key(&word.to_lowercase()) && !proc_param_names.contains(&word.to_lowercase()) {
                       all_names.insert(crate::naming::snake_to_camel(word).to_lowercase());
                   }
               }
                let local_var_names: std::collections::HashSet<String> = proc.local_vars.keys().map(|k| k.to_lowercase()).collect();
                for caps in re.captures_iter(&dml.sql_text) {
                    let word = caps.get(1).unwrap().as_str();
                    if pkg.package_vars.contains_key(word) && !local_var_names.contains(&word.to_lowercase()) && !proc_param_names.contains(&word.to_lowercase()) {
                       all_names.insert(crate::naming::snake_to_camel(word).to_lowercase());
                   }
               }
               for p in &proc.parameters {
                   if p.is_out() && case_insensitive_word_match(&dml.sql_text, &p.name) {
                       all_names.insert(crate::naming::snake_to_camel(&p.name).to_lowercase());
                   }
               }

              let total_params = all_names.len();
              all_dmls.push((dml.method_id.clone(), dml.sql_type, total_params, dml.returns_list, dml.result_type.clone(), dml.sql_text.clone(), param_types));
          }
      }

    let mut lines = Vec::new();
    for (method_id, sql_type, param_count, returns_list, result_type, sql_text, param_types) in &all_dmls {
        let is_overloaded = all_dmls.iter()
            .filter(|(id, _, _, _, _, _, pts)| id == method_id && pts != param_types)
            .count() > 0;
        let any_args = if *param_count > 0 {
            if is_overloaded && !param_types.is_empty() {
                let mut args: Vec<String> = param_types.iter()
                    .map(|t| format!("({}) any()", any_matcher_type(t)))
                    .collect();
                while args.len() < *param_count {
                    args.push("any()".to_string());
                }
                args.join(", ")
            } else {
                (0..*param_count).map(|_| "any()".to_string()).collect::<Vec<_>>().join(", ")
            }
        } else {
            String::new()
        };
        match sql_type {
            DmlType::Select => {
                let is_scalar = result_type.as_ref().map_or(false, |rt| {
                    !rt.contains("Map<") && !rt.contains("List<")
                });
                if *returns_list {
                    let cols = extract_select_columns(sql_text);
                    if cols.is_empty() {
                        let mut mock = build_map_mock(&extra_keys);
                        if extra_keys.is_empty() { mock.push_str("m.put(\"id\", 1L); "); }
                        mock.push_str(&format!("when({}.{}({})).thenReturn(java.util.List.of(m)); }}", mapper_name, method_id, any_args));
                        lines.push(format!("        {}", mock));
                    } else {
                        let mut mock = build_map_mock(&cols);
                        mock.push_str(&format!("when({}.{}({})).thenReturn(java.util.List.of(m)); }}", mapper_name, method_id, any_args));
                        lines.push(format!("        {}", mock));
                    }
                } else if is_scalar {
                    let rt = result_type.as_ref().unwrap();
                    let scalar_val = if rt == "Object" {
                        "999".to_string()
                    } else {
                        scalar_mock_value(rt)
                    };
                    lines.push(format!(
                        "        when({}.{}({})).thenReturn({});",
                        mapper_name, method_id, any_args, scalar_val
                    ));
                } else {
                    let cols = extract_select_columns(sql_text);
                    if cols.is_empty() {
                        let mut mock = build_map_mock(&extra_keys);
                        if extra_keys.is_empty() { mock.push_str("m.put(\"id\", 1L); "); }
                        mock.push_str(&format!("when({}.{}({})).thenReturn(m); }}", mapper_name, method_id, any_args));
                        lines.push(format!("        {}", mock));
                    } else {
                        let mut mock = build_map_mock(&cols);
                        for k in &extra_keys {
                            if !cols.iter().any(|c| crate::naming::snake_to_camel(c) == *k) {
                                mock.push_str(&format!("m.put(\"{}\", {}); ", escape_java_string(k), column_mock_value_for_key(k)));
                            }
                        }
                        mock.push_str(&format!("when({}.{}({})).thenReturn(m); }}", mapper_name, method_id, any_args));
                        lines.push(format!("        {}", mock));
                    }
                }
            }
            _ => {
                lines.push(format!(
                    "        when({}.{}({})).thenReturn(1);",
                    mapper_name, method_id, any_args
                ));
            }
        }
    }
    lines
}

fn default_test_value(java_type: &str, param_name: &str) -> String {
    let tl = java_type.to_lowercase();
    let nl = param_name.to_lowercase();
    if tl.contains("long") {
        if nl.contains("id") { return "1L".to_string(); }
        return "100L".to_string();
    }
    if tl.contains("integer") || tl == "int" {
        if nl.contains("qty") || nl.contains("limit") { return "5".to_string(); }
        return "1".to_string();
    }
    if tl.contains("bigdecimal") { return "new java.math.BigDecimal(\"99.99\")".to_string(); }
    if tl.contains("double") { return "1.0d".to_string(); }
    if tl.contains("float") { return "1.0f".to_string(); }
    if tl.contains("boolean") { return "true".to_string(); }
    if tl.contains("timestamp") { return "java.sql.Timestamp.valueOf(\"2024-01-01 00:00:00\")".to_string(); }
    if tl.contains("date") { return "java.sql.Date.valueOf(\"2024-01-01\")".to_string(); }
    if tl.contains("map") { return "new java.util.HashMap<>()".to_string(); }
    if tl == "object" {
        if nl.contains("spectrum") {
            return "java.util.Arrays.asList(1.0, 2.0, 3.0, 2.5, 1.5, 0.5, 1.0, 2.0, 3.0, 1.0)".to_string();
        }
        if nl.contains("array") || nl.contains("list") || nl.ends_with("arr") || nl.contains("_arr") {
            return "java.util.Arrays.asList(1.0, 2.0, 3.0)".to_string();
        }
        return "new java.util.HashMap<String, Object>()".to_string();
    }
    if tl.contains("string") {
        if nl.contains("date") { return "\"2024-01-01\"".to_string(); }
        if nl.contains("ids") || nl.contains("list") { return "\"1,2,3\"".to_string(); }
        if ["flag", "amount", "seqno", "seq", "interfaceseq", "operflag", "stepno", "count", "quantity", "qty", "price", "total"].iter().any(|kw| nl.contains(kw)) {
            return "\"1\"".to_string();
        }
    }
    format!("\"test_{}\"", param_name)
}

fn scalar_mock_value(java_type: &str) -> String {
    let tl = java_type.to_lowercase();
    if tl.contains("long") { return "999L".to_string(); }
    if tl.contains("integer") || tl == "int" { return "999".to_string(); }
    if tl.contains("bigdecimal") { return "new java.math.BigDecimal(\"999.99\")".to_string(); }
    if tl.contains("double") { return "999.0d".to_string(); }
    if tl.contains("float") { return "999.0f".to_string(); }
    if tl.contains("boolean") { return "true".to_string(); }
    if tl.contains("string") { return "\"test_value\"".to_string(); }
    if tl.contains("timestamp") { return "java.sql.Timestamp.valueOf(\"2024-01-01 00:00:00\")".to_string(); }
    if tl.contains("date") { return "java.sql.Date.valueOf(\"2024-01-01\")".to_string(); }
    "null".to_string()
}

fn lowercase_first(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
        None => String::new(),
    }
}

fn any_matcher_type(java_type: &str) -> &str {
    match java_type {
        "String" => "String",
        "Integer" | "int" => "Integer",
        "Long" | "long" => "Long",
        "Boolean" => "Boolean",
        "BigDecimal" | "java.math.BigDecimal" => "java.math.BigDecimal",
        "Date" | "java.sql.Date" => "java.sql.Date",
        "Timestamp" | "java.sql.Timestamp" => "java.sql.Timestamp",
        "Map<String, Object>" | "java.util.Map" => "java.util.Map",
        _ => "Object",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{DmlStatement, DmlType, Parameter, ParamMode, ProcedureInfo};

    fn make_pkg(name: &str, procs: Vec<ProcedureInfo>) -> PackageInfo {
        PackageInfo {
                    package_name: name.to_string(),
                    procedures: procs,
                    table_refs: Default::default(),
                    package_vars: Default::default(),
                    source_file: String::new(),
                    comments: Vec::new(),
                    java_package: String::new(),
                    custom_types: Default::default(),
                    extra_mapper_methods: Vec::new(),
                }
    }

    fn make_proc(name: &str) -> ProcedureInfo {
        ProcedureInfo::new(format!("pkg.{}", name), "pkg".to_string(), name.to_string())
    }

    #[test]
    fn test_basic_test_class() {
        let proc = make_proc("do_work");
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/OrderServiceTest.java"),
        ).unwrap();
        assert!(content.contains("@ExtendWith(MockitoExtension.class)"));
        assert!(content.contains("class OrderServiceTest"));
        assert!(content.contains("@Mock"));
        assert!(content.contains("private OrderMapper orderMapper;"));
        assert!(content.contains("@InjectMocks"));
        assert!(content.contains("private OrderService service;"));
        assert!(content.contains("test_doWork_success"));
    }

    #[test]
    fn test_mock_insert_dml() {
        let mut proc = make_proc("create_order");
        proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Insert,
                    method_id: "insertOrder".to_string(),
                    sql_text: "insert into t values(1)".to_string(),
                    result_type: None,
                    ..Default::default()
                });
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/OrderServiceTest.java"),
        ).unwrap();
        assert!(content.contains("when(orderMapper.insertOrder()).thenReturn(1);"));
    }

    #[test]
    fn test_mock_select_dml() {
        let mut proc = make_proc("get_data");
        proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: "selectData".to_string(),
                    sql_text: "select * from t".to_string(),
                    result_type: None,
                    ..Default::default()
                });
        let pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/DataServiceTest.java"),
        ).unwrap();
        assert!(content.contains("HashMap<String,Object>"));
        assert!(content.contains("selectData"));
    }

    #[test]
    fn test_mock_select_dml_returns_list() {
        let mut proc = make_proc("list_data");
        proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: "selectListData".to_string(),
                    sql_text: "select * from t".to_string(),
                    result_type: None,
                    returns_list: true,
                    ..Default::default()
                });
        let pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/DataServiceTest.java"),
        ).unwrap();
        assert!(content.contains("when(dataMapper.selectListData()).thenReturn(java.util.List.of(m))"));
    }

    #[test]
    fn test_mock_select_dml_scalar_integer() {
        let mut proc = make_proc("check_stock");
        proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: "selectCheckStock".to_string(),
                    sql_text: "select count(*) into v_count from t".to_string(),
                    result_type: Some("Integer".to_string()),
                    ..Default::default()
                });
        let pkg = make_pkg("pkg_inventory", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/InventoryServiceTest.java"),
        ).unwrap();
        assert!(content.contains("when(inventoryMapper.selectCheckStock()).thenReturn(999)"));
        assert!(!content.contains("HashMap"));
    }

    #[test]
    fn test_mock_select_dml_scalar_long() {
        let mut proc = make_proc("get_id");
        proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: "selectGetId".to_string(),
                    sql_text: "select seq.nextval into v_id from dual".to_string(),
                    result_type: Some("Long".to_string()),
                    ..Default::default()
                });
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/OrderServiceTest.java"),
        ).unwrap();
        assert!(content.contains("when(orderMapper.selectGetId()).thenReturn(999L)"));
        assert!(!content.contains("HashMap"));
    }

    #[test]
    fn test_default_test_values() {
        assert_eq!(default_test_value("Long", "pOrderId"), "1L");
        assert_eq!(default_test_value("long", "pCount"), "100L");
        assert_eq!(default_test_value("Integer", "pQty"), "5");
        assert_eq!(default_test_value("int", "pStatus"), "1");
        assert_eq!(default_test_value("String", "pName"), "\"test_pName\"");
        assert_eq!(default_test_value("boolean", "pFlag"), "true");
    }

    #[test]
    fn test_extract_select_columns_basic() {
        let sql = "select id, name, salary from t_employees where dept_id = p_dept_id order by id";
        let cols = extract_select_columns(sql);
        assert_eq!(cols, vec!["id", "name", "salary"]);
    }

    #[test]
    fn test_extract_select_columns_star() {
        let sql = "select * from t";
        let cols = extract_select_columns(sql);
        assert!(cols.is_empty());
    }

    #[test]
    fn test_extract_select_columns_alias() {
        let sql = "select e.id, e.name as emp_name, count(*) as cnt from emp e";
        let cols = extract_select_columns(sql);
        assert_eq!(cols, vec!["id", "emp_name", "cnt"]);
    }

    #[test]
    fn test_extract_select_columns_distinct() {
        let sql = "select distinct dept_id, dept_name from departments";
        let cols = extract_select_columns(sql);
        assert_eq!(cols, vec!["dept_id", "dept_name"]);
    }

    #[test]
    fn test_extract_select_columns_no_from() {
        let sql = "insert into t values(1)";
        let cols = extract_select_columns(sql);
        assert!(cols.is_empty());
    }

    #[test]
    fn test_column_mock_value_patterns() {
        assert_eq!(column_mock_value("id"), "1L");
        assert_eq!(column_mock_value("order_id"), "1L");
        assert_eq!(column_mock_value("order_no"), "1L");
        assert_eq!(column_mock_value("name"), "\"test\"");
        assert_eq!(column_mock_value("status_code"), "\"test\"");
        assert_eq!(column_mock_value("salary"), "java.math.BigDecimal.TEN");
        assert_eq!(column_mock_value("total_amount"), "java.math.BigDecimal.TEN");
        assert_eq!(column_mock_value("created_date"), "java.sql.Date.valueOf(\"2024-01-01\")");
        assert_eq!(column_mock_value("is_active"), "true");
        assert_eq!(column_mock_value("age"), "1");
        assert_eq!(column_mock_value("unknown_col"), "1L");
    }

    #[test]
    fn test_mock_select_with_columns() {
        let mut proc = make_proc("get_data");
        proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: "selectData".to_string(),
                    sql_text: "select id, name, salary from t_employees".to_string(),
                    result_type: Some("Map<String, Object>".to_string()),
                    ..Default::default()
                });
        let pkg = make_pkg("pkg_data", vec![proc]);
        let lines = mock_all_mapper_methods("dataMapper", &pkg);
        let joined = lines.join("\n");
        assert!(joined.contains("m.put(\"id\", 1L)"));
        assert!(joined.contains("m.put(\"name\", \"test\")"));
        assert!(joined.contains("m.put(\"salary\", java.math.BigDecimal.TEN)"));
        assert!(joined.contains("when(dataMapper.selectData()).thenReturn(m)"));
    }

    #[test]
    fn test_mock_select_returns_list_with_columns() {
        let mut proc = make_proc("list_data");
        proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: "selectListData".to_string(),
                    sql_text: "select id, name from t".to_string(),
                    result_type: None,
                    returns_list: true,
                    ..Default::default()
                });
        let pkg = make_pkg("pkg_data", vec![proc]);
        let lines = mock_all_mapper_methods("dataMapper", &pkg);
        let joined = lines.join("\n");
        assert!(joined.contains("m.put(\"id\", 1L)"));
        assert!(joined.contains("m.put(\"name\", \"test\")"));
        assert!(joined.contains("when(dataMapper.selectListData()).thenReturn(java.util.List.of(m))"));
    }
}
