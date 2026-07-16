use std::collections::BTreeSet;
use std::path::Path;

use encoding_rs::Encoding;

use crate::generate::mapper::{is_simple_java_type, resolve_import};
use crate::generate::writer::CodeWriter;
use crate::naming::{java_method_name, package_to_classname, snake_to_camel, snake_to_pascal};
use crate::type_map::sql_type_to_java;
use crate::types::{DmlType, PackageInfo, ParamMode};

pub fn write_service_class(
    base_path: &Path,
    pkg: &mut PackageInfo,
    base_package: &str,
    service_injections: &std::collections::HashMap<String, String>,
    encoding: &'static Encoding,
    debug: bool,
) -> std::io::Result<String> {
    let java_pkg = format!("{}.service", base_package);
    let svc_dir = base_path.join(format!("src/main/java/{}/service", base_package.replace('.', "/")));
    let class_name = format!("{}Service", package_to_classname(&pkg.package_name));
    let mapper_var = format!(
        "{}Mapper",
        {
            let cn = package_to_classname(&pkg.package_name);
            let mut c = cn.chars();
            match c.next() {
                Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                None => String::new(),
            }
        }
    );

    let mut all_imports: BTreeSet<String> = BTreeSet::new();
    all_imports.insert(format!(
        "import {}.mapper.{}Mapper;",
        base_package,
        package_to_classname(&pkg.package_name)
    ));
    all_imports.insert(format!("import {}.exception.BusinessException;", base_package));
    all_imports.insert("import org.slf4j.Logger;".to_string());
    all_imports.insert("import org.slf4j.LoggerFactory;".to_string());
    all_imports.insert("import org.springframework.stereotype.Service;".to_string());
    all_imports.insert("import org.springframework.transaction.annotation.Transactional;".to_string());

    for proc in &pkg.procedures {
        all_imports.extend(proc.imports.iter().cloned());
        for p in &proc.parameters {
            if let Some(imp) = resolve_import(&p.java_type) {
                all_imports.insert(imp);
            }
        }
    }

    for (svc_var, pkg_name) in service_injections {
        let svc_class = if !pkg_name.is_empty() {
            format!("{}Service", package_to_classname(pkg_name))
        } else {
            let part = svc_var.replace("Service", "");
            format!("{}Service", package_to_classname(&part))
        };
        all_imports.insert(format!("import {}.service.{};", base_package, svc_class));
    }

    let all_body: String = pkg
        .procedures
        .iter()
        .flat_map(|p| p.java_logic_lines.iter().cloned())
        .collect::<Vec<_>>()
        .join(" ");
    let has_list_return = pkg.procedures.iter().any(|p| {
        if p.is_function {
            p.return_type.as_ref().map_or(false, |t| t.contains("List"))
        } else {
            p.parameters.iter().any(|pp| pp.is_out() && pp.is_refcursor())
        }
    });
    let has_map_in_body = all_body.contains("Map<String") || has_list_return;
    let has_cursor_results = pkg.procedures.iter().any(|p| !p.open_cursors.is_empty());
    if all_body.contains("List<") || has_list_return || has_cursor_results {
        all_imports.insert("import java.util.List;".to_string());
    }
    if has_map_in_body || has_cursor_results {
        all_imports.insert("import java.util.Map;".to_string());
    }
    if all_body.contains("HashMap<") || all_body.contains("new HashMap") {
        all_imports.insert("import java.util.HashMap;".to_string());
    }
    if all_body.contains("BigDecimal") {
        all_imports.insert("import java.math.BigDecimal;".to_string());
    }
    if all_body.contains("Arrays.") {
        all_imports.insert("import java.util.Arrays;".to_string());
    }
    if all_body.contains("AtomicReference<")
        || pkg.procedures.iter().any(|p| p.parameters.iter().any(|p| p.is_out()))
    {
        all_imports.insert("import java.util.concurrent.atomic.AtomicReference;".to_string());
    }
    let has_map_var = pkg.procedures.iter().any(|p| {
        p.local_vars.values().any(|t| t.contains("Map<String"))
    });
    if has_map_var {
        all_imports.insert("import java.util.HashMap;".to_string());
    }

    let mut w = CodeWriter::new();
    w.line(&format!("package {};", java_pkg));
    w.blank();
    for imp in &all_imports {
        w.line(imp);
    }
    w.blank();
    w.line("@Service");
    if !pkg.source_file.is_empty() {
        w.line(&format!("// Source: {}", pkg.source_file));
    }
    w.line(&format!("public class {} {{", class_name));
    w.push_indent();
    w.line(&format!(
        "private static final Logger log = LoggerFactory.getLogger({}.class);",
        class_name
    ));
    w.blank();

    w.line(&format!(
        "private final {} {};",
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
        w.line(&format!("private final {} {};", svc_class, svc_var));
    }
    // Collect all package variable names that are written in any procedure
    let written_package_vars: std::collections::HashSet<&str> = pkg.procedures.iter()
        .flat_map(|p| p.written_package_vars.iter().map(|s| s.as_str()))
        .collect();
    for (var_name, var_info) in &pkg.package_vars {
        let field_name = crate::naming::java_safe_identifier(&crate::naming::snake_to_camel(var_name));
        let is_readonly = var_info.is_constant || !written_package_vars.contains(var_name.as_str());
        let modifier = if is_readonly { "private static final" } else { "private static" };
        let default_val = var_info.default_value.as_deref().unwrap_or_else(|| default_for_type(&var_info.java_type));
        let coerced_default = coerce_default_value(&var_info.java_type, default_val);
        w.line(&format!("{} {} {} = {};", modifier, var_info.java_type, field_name, coerced_default));
        if var_info.java_type == "BigDecimal" {
            all_imports.insert("import java.math.BigDecimal;".to_string());
        }
    }
    w.blank();

    let mut constructor_params = vec![format!(
        "{} {}",
        format!("{}Mapper", package_to_classname(&pkg.package_name)),
        mapper_var
    )];
    let mut constructor_assigns = vec![format!("        this.{} = {};", mapper_var, mapper_var)];
    for (svc_var, _pkg_name) in service_injections {
        let svc_class = if let Some(pn) = service_injections.get(svc_var) {
            if !pn.is_empty() {
                format!("{}Service", package_to_classname(pn))
            } else {
                let part = svc_var.replace("Service", "");
                format!("{}Service", package_to_classname(&part))
            }
        } else {
            let part = svc_var.replace("Service", "");
            format!("{}Service", package_to_classname(&part))
        };
        constructor_params.push(format!("{} {}", svc_class, svc_var));
        constructor_assigns.push(format!("        this.{} = {};", svc_var, svc_var));
    }
    w.line(&format!("public {}({}) {{", class_name, constructor_params.join(", ")));
    for assign in &constructor_assigns {
        w.line(assign);
    }
    w.line("}");

    // Generate inner static classes for RECORD custom types
    for (type_name, type_info) in &pkg.custom_types {
        if type_info.is_record && !type_info.fields.is_empty() {
            let inner_cls = custom_type_classname(type_name);
            w.blank();
            w.line(&format!("public static class {} {{", inner_cls));
            w.push_indent();
            for (fld_name, fld_java_type) in &type_info.fields {
                let fld_java = snake_to_camel(fld_name);
                w.line(&format!("public {} {};", fld_java_type, fld_java));
            }
            w.pop_indent();
            w.line("}");
        }
    }

    let object_pkg_var_names: Vec<String> = pkg.package_vars.iter()
        .filter(|(_, v)| v.java_type == "Object")
        .map(|(name, _)| snake_to_camel(name))
        .collect();

    let has_any_array_vars = pkg.procedures.iter().any(|p| p.has_array_vars);

    for proc in &pkg.procedures {
        w.blank();
            let method = build_service_method(proc, &mapper_var, &object_pkg_var_names, &pkg.package_vars, debug);
        for line in method.split('\n') {
            w.line(line);
        }
    }

    if has_any_array_vars {
        w.blank();
        w.line("public java.util.List<String> stringToArray(String str, String delimiter) {");
        w.line("    if (str == null || str.isEmpty()) return java.util.Collections.emptyList();");
        w.line("    return java.util.Arrays.asList(str.split(java.util.regex.Pattern.quote(delimiter)));");
        w.line("}");
    }

    let all_body: String = pkg.procedures.iter()
        .flat_map(|p| p.java_logic_lines.iter())
        .cloned()
        .collect::<Vec<_>>()
        .join(" ");
    if all_body.contains("this.jsonbArrayLength(") {
        w.blank();
        w.line("public Integer jsonbArrayLength(String jsonb) {");
        w.line("    try {");
        w.line("        return new com.fasterxml.jackson.databind.ObjectMapper().readValue(jsonb, com.fasterxml.jackson.databind.JsonNode.class).size();");
        w.line("    } catch (Exception e) { return 0; }");
        w.line("}");
    }
    if all_body.contains("this.jsonbBuildObject(") {
        w.blank();
        w.line("public String jsonbBuildObject(Object... args) {");
        w.line("    java.util.Map<String, Object> map = new java.util.LinkedHashMap<>();");
        w.line("    for (int i = 0; i + 1 < args.length; i += 2) {");
        w.line("        map.put(String.valueOf(args[i]), args[i + 1]);");
        w.line("    }");
        w.line("    try { return new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(map); }");
        w.line("    catch (Exception e) { throw new RuntimeException(e); }");
        w.line("}");
    }
    if all_body.contains("this._md5(") {
        w.blank();
        w.line("private String _md5(String input) {");
        w.line("    try {");
        w.line("        return String.format(\"%032x\", new java.math.BigInteger(1, java.security.MessageDigest.getInstance(\"MD5\").digest(input.getBytes())));");
        w.line("    } catch (java.security.NoSuchAlgorithmException e) {");
        w.line("        throw new RuntimeException(e);");
        w.line("    }");
        w.line("}");
    }
    if all_body.contains("this.jsonbGet(") {
        w.blank();
        w.line("public Object jsonbGet(String jsonb, Object key) {");
        w.line("    try {");
        w.line("        com.fasterxml.jackson.databind.JsonNode node = new com.fasterxml.jackson.databind.ObjectMapper().readValue(jsonb, com.fasterxml.jackson.databind.JsonNode.class);");
        w.line("        if (key instanceof Integer) return node.get((Integer) key);");
        w.line("        return node.get(String.valueOf(key));");
        w.line("    } catch (Exception e) { return null; }");
        w.line("}");
    }
    if all_body.contains("this.jsonbGetText(") {
        w.blank();
        w.line("public String jsonbGetText(String jsonb, Object key) {");
        w.line("    try {");
        w.line("        com.fasterxml.jackson.databind.JsonNode node = new com.fasterxml.jackson.databind.ObjectMapper().readValue(jsonb, com.fasterxml.jackson.databind.JsonNode.class);");
        w.line("        if (key instanceof Integer) return node.get((Integer) key).asText();");
        w.line("        return node.get(String.valueOf(key)).asText();");
        w.line("    } catch (Exception e) { return null; }");
        w.line("}");
    }
    if all_body.contains("this._crc32(") {
        w.blank();
        w.line("private int _crc32(String input) {");
        w.line("    java.util.zip.CRC32 crc = new java.util.zip.CRC32();");
        w.line("    crc.update(input.getBytes());");
        w.line("    return (int) crc.getValue();");
        w.line("}");
    }
    if all_body.contains("_appendList(") {
        w.blank();
        w.line("private <T> java.util.List<T> _appendList(java.util.List<T> list, T element) {");
        w.line("    list.add(element);");
        w.line("    return list;");
        w.line("}");
    }
    if all_body.contains("this.nextval(") {
        w.blank();
        w.line(&format!("public Long nextval(String seqName) {{"));
        w.line(&format!("    return {}.selectNextval(seqName);", mapper_var));
        w.line("}");
        pkg.extra_mapper_methods.push(("selectNextval".into(), "SELECT nextval(#{seqName,jdbcType=VARCHAR}) AS val".into(), "Long".into()));
    }
    if all_body.contains("this.currval(") {
        w.blank();
        w.line(&format!("public Long currval(String seqName) {{"));
        w.line(&format!("    return {}.selectCurrval(seqName);", mapper_var));
        w.line("}");
        pkg.extra_mapper_methods.push(("selectCurrval".into(), "SELECT currval(#{seqName,jdbcType=VARCHAR}) AS val".into(), "Long".into()));
    }

    w.pop_indent();
    w.line("}");

    std::fs::create_dir_all(&svc_dir)?;
    let file_path = svc_dir.join(format!("{}.java", class_name));
    w.write_to_file(&file_path, encoding)?;
    Ok(class_name)
}

fn boxed_to_primitive(t: &str) -> &str {
    match t {
        "Long" => "long",
        "Integer" => "int",
        "Boolean" => "boolean",
        "Double" => "double",
        "Float" => "float",
        _ => t,
    }
}

fn default_for_type(t: &str) -> &'static str {
    let tl = t.to_lowercase();
    if tl.contains("long") { return "0L"; }
    if tl.contains("integer") || tl == "int" { return "0"; }
    if tl.contains("bigdecimal") { return "java.math.BigDecimal.ZERO"; }
    if tl.contains("double") { return "0.0d"; }
    if tl.contains("float") { return "0.0f"; }
    if tl.contains("boolean") { return "false"; }
    if tl.starts_with("map<") { return "new HashMap<>()"; }
    if tl.starts_with("atomicreference") { return "new AtomicReference<>(null)"; }
    "null"
}

fn coerce_default_value(java_type: &str, default_val: &str) -> String {
    let trimmed = default_val.trim();
    let tl = java_type.to_lowercase();
    if tl.contains("list") || tl.contains("array") {
        if trimmed.starts_with('"') {
            return "new java.util.ArrayList<>()".to_string();
        }
        return default_val.to_string();
    }
    if tl.contains("bigdecimal") {
        if trimmed == "0" {
            return "java.math.BigDecimal.ZERO".to_string();
        }
        if trimmed == "1" {
            return "java.math.BigDecimal.ONE".to_string();
        }
        if trimmed == "null" || trimmed == "java.math.bigdecimal.zero" || trimmed == "java.math.bigdecimal.one" {
            return default_val.to_string();
        }
        if trimmed.parse::<i64>().is_ok() {
            return format!("java.math.BigDecimal.valueOf({})", trimmed);
        }
        if trimmed.parse::<f64>().is_ok() {
            return format!("new java.math.BigDecimal(\"{}\")", trimmed);
        }
        if trimmed.starts_with("(") && trimmed.ends_with(")") {
            let inner = &trimmed[1..trimmed.len()-1];
            if inner.parse::<i64>().is_ok() || inner.parse::<f64>().is_ok() {
                return format!("java.math.BigDecimal.valueOf({})", inner);
            }
        }
        return default_val.to_string();
    }
    if tl.contains("long") {
        if trimmed.parse::<i64>().is_ok() && !trimmed.ends_with('l') && !trimmed.ends_with('L') {
            return format!("{}L", trimmed);
        }
    }
    if tl.contains("double") {
        if trimmed.parse::<f64>().is_ok() && !trimmed.ends_with('d') && !trimmed.ends_with('D') && !trimmed.contains('.') {
            return format!("{}d", trimmed);
        }
    }
    if tl.contains("float") {
        if trimmed.parse::<f64>().is_ok() && !trimmed.ends_with('f') && !trimmed.ends_with('F') {
            return format!("{}f", trimmed);
        }
    }
    default_val.to_string()
}

/// Convert a SQL custom type name to a Java class name for inner static classes.
/// Strips "t_" or "type_" prefix, then applies PascalCase.
fn custom_type_classname(sql_type_name: &str) -> String {
    let name = sql_type_name.to_lowercase();
    let stripped = if name.starts_with("t_") {
        &sql_type_name[2..]
    } else if name.starts_with("type_") {
        &sql_type_name[5..]
    } else {
        sql_type_name
    };
    snake_to_pascal(stripped)
}

/// Format a SQL CommentBlock as a Java comment line.
fn format_comment_for_java(comment: &crate::types::CommentBlock) -> String {
    let text = comment.text.trim();
    let stripped = if text.starts_with("--") {
        text[2..].trim().to_string()
    } else if text.starts_with("/*") && text.ends_with("*/") {
        let inner = text[2..text.len()-2].trim();
        inner.lines()
            .map(|l| l.trim())
            .filter(|l| !l.is_empty())
            .collect::<Vec<_>>()
            .join(" ")
    } else {
        text.to_string()
    };
    if stripped.is_empty() {
        String::new()
    } else {
        format!("// {}", stripped)
    }
}

pub(crate) fn should_stub_procedure(proc: &crate::types::ProcedureInfo, _object_pkg_var_names: &[String]) -> bool {
    let lines = &proc.java_logic_lines;

    let has_goto = lines.iter().any(|l| l.trim().starts_with("// GOTO "));
    if has_goto {
        return true;
    }

    let broken_java = lines.iter().any(|l| {
        let t = l.trim();
        t.contains("null ^ ") || t.contains("null > null") || t.contains("null < null")
            || t.contains("if (1)") || t.contains("if (2)") || t.contains("if (3)")
            || t.contains("Objects.equals(null,")
            || t.contains("((Object)")
            || t.contains("(Object[])")
            || t.contains("Math.pow(null,")
            || t.contains("/* ENCODE */")
            || (t.contains(".set((-") && t[t.find(".set((-").unwrap() + 7..].chars().next().map_or(false, |c| !c.is_ascii_digit()))
            || t.contains("Math.abs(") && t.contains(".compareTo(")
            || t.contains(".longValue() / 0")
            || t.contains("Timestamp(System.currentTimeMillis()) - ")
            || t.contains("((Long) ") && t.contains(".get(")
    });
    if broken_java {
        return true;
    }

    let broken_defaults = proc.local_var_defaults.values().any(|v| v.contains("((Object)") || v.contains(">>") || v.contains("-> ") || v.contains("(Object[])"));
    if broken_defaults {
        return true;
    }

    let has_dynamic_sql = lines.iter().any(|l| {
        let t = l.trim();
        t.contains("execute immediate") || t.contains("EXECUTE IMMEDIATE")
    });
    if has_dynamic_sql {
        return true;
    }

    false
}

fn build_service_method(
    proc: &crate::types::ProcedureInfo,
    mapper_name: &str,
    object_pkg_var_names: &[String],
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
    debug: bool,
) -> String {
    let mut params: Vec<String> = Vec::new();
    let mut out_params: Vec<&crate::types::Parameter> = Vec::new();

    for p in &proc.parameters {
        if p.is_out() {
            if p.is_refcursor() {
                continue;
            }
             params.push(format!("AtomicReference<{}> {}", p.java_type, snake_to_camel(&p.name)));
             out_params.push(p);
         } else {
             let is_null_default = p.default_value.as_ref().map_or(false, |dv| dv.to_lowercase() == "null");
             let param_type = if is_null_default { &p.java_type } else { boxed_to_primitive(&p.java_type) };
             params.push(format!("{} {}", param_type, snake_to_camel(&p.name)));
         }
     }

     let params_str = params.join(", ");

    let mut ret_type = if proc.is_function {
        match &proc.return_type {
            Some(rt) => {
                if rt.chars().next().map_or(false, |c| c.is_uppercase()) || rt.contains('.') {
                    rt.clone()
                } else {
                    sql_type_to_java(rt).map(|s| s.to_string())
                        .unwrap_or_else(|| "Object".to_string())
                }
            }
            None => "Object".to_string(),
        }
    } else {
        let has_refcursor = proc.parameters.iter().any(|p| p.is_out() && p.is_refcursor());
        if has_refcursor {
            "List<Map<String, Object>>".to_string()
        } else {
            "void".to_string()
        }
    };

    if ret_type == "Object" {
        let logic_text = proc.java_logic_lines.join(" ");
        let method_var_patterns = [".put(", ".getOrDefault(", ".get(\""];
        if method_var_patterns.iter().any(|p| logic_text.contains(p)) {
            ret_type = "Map<String, Object>".to_string();
        }
    }

    let method_name = java_method_name(&proc.proc_name);
    let has_dml = proc.dml_statements.iter().any(|d| matches!(d.sql_type, DmlType::Insert | DmlType::Update | DmlType::Delete));

    let mut body_lines: Vec<String> = Vec::new();

    if should_stub_procedure(proc, object_pkg_var_names) {
        body_lines.push("// TODO: Auto-generated stub — complex PL/pgSQL pattern requires manual implementation".to_string());
        if ret_type != "void" {
            body_lines.push("return null;".to_string());
        }
    } else {
        let out_java_names: std::collections::HashSet<String> =
            out_params.iter().map(|p| snake_to_camel(&p.name)).collect();

        for (var_name, var_type) in &proc.local_vars {
            let var_java = snake_to_camel(var_name);
            if !out_java_names.contains(&var_java) {
                let is_loop_iter = proc.java_logic_lines.iter().any(|l| {
                    l.contains(&format!("for ({} ", var_type)) && l.contains(&format!(" : {}List)", var_java))
                });
                if is_loop_iter {
                    continue;
                }
                // Check if this local var was promoted to AtomicReference for OUT param usage
                if let Some(inner_type) = proc.out_local_vars.get(&var_name.to_lowercase()) {
                    body_lines.push(format!("AtomicReference<{}> {} = new AtomicReference<>(null);", inner_type, var_java));
                } else {
                    let default_val = proc.local_var_defaults.get(&var_name.to_lowercase())
                        .cloned()
                        .unwrap_or_else(|| default_for_type(var_type).to_string());
                    let coerced = coerce_default_value(var_type, &default_val);
                    let is_object_used_as_map = var_type == "Object" && proc.java_logic_lines.iter()
                        .any(|l| {
                            l.contains(&format!("((java.util.Map<String, Object>) {}).put(", var_java)) ||
                            l.contains(&format!("{}.getOrDefault(", var_java)) ||
                            l.contains(&format!("{}.put(", var_java)) ||
                            l.contains(&format!("{}.get(\"", var_java))
                        });
                    let is_object_used_as_list = var_type == "Object" && proc.java_logic_lines.iter()
                        .any(|l| l.contains(&format!("((java.util.List<?>) {})", var_java)));
                    if is_object_used_as_map {
                        if debug {
                            if let Some(&line) = proc.local_var_source_lines.get(&var_name.to_lowercase()) {
                                let src_path = if !proc.source_path.is_empty() { &proc.source_path } else { &proc.source_file };
                                body_lines.push(crate::debug::format_debug_comment(src_path, line, 100));
                            }
                        }
                        body_lines.push(format!("java.util.Map<String, Object> {} = new java.util.HashMap<>();", var_java));
                    } else if is_object_used_as_list {
                        if debug {
                            if let Some(&line) = proc.local_var_source_lines.get(&var_name.to_lowercase()) {
                                let src_path = if !proc.source_path.is_empty() { &proc.source_path } else { &proc.source_file };
                                body_lines.push(crate::debug::format_debug_comment(src_path, line, 100));
                            }
                        }
                        body_lines.push(format!("java.util.List<Object> {} = new java.util.ArrayList<>();", var_java));
                    } else {
                        if debug {
                            if let Some(&line) = proc.local_var_source_lines.get(&var_name.to_lowercase()) {
                                let src_path = if !proc.source_path.is_empty() { &proc.source_path } else { &proc.source_file };
                                body_lines.push(crate::debug::format_debug_comment(src_path, line, 100));
                            }
                        }
                        body_lines.push(format!("{} {} = {};", var_type, var_java, coerced));
                    }
                }
            }
        }

        let refcursor_outs: Vec<_> = proc.parameters.iter()
            .filter(|p| p.is_out() && p.is_refcursor())
            .collect();
        let refcursor_names: std::collections::HashSet<String> = refcursor_outs.iter()
            .map(|p| snake_to_camel(&p.name))
            .collect();
        let mut declared_cursor_vars: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut declared_cursor_idxs: std::collections::HashSet<String> = std::collections::HashSet::new();
        for rc_out in &refcursor_outs {
            let rc_java = snake_to_camel(&rc_out.name);
            if let Some(cursor_info) = proc.open_cursors.get(&rc_java) {
                if let Some(ref result_var) = cursor_info.result_var {
                    if declared_cursor_vars.insert(result_var.clone()) {
                        body_lines.push(format!("List<Map<String, Object>> {} = null;", result_var));
                    }
                }
            } else {
                let var_name = format!("{}Result", rc_java);
                if declared_cursor_vars.insert(var_name.clone()) {
                    body_lines.push(format!("List<Map<String, Object>> {} = java.util.Collections.emptyList();", var_name));
                }
            }
        }
        for (cursor_name, cursor_info) in &proc.open_cursors {
            if !refcursor_names.contains(cursor_name) {
                if let Some(ref result_var) = cursor_info.result_var {
                    if declared_cursor_vars.insert(result_var.clone()) {
                        body_lines.push(format!("List<Map<String, Object>> {} = null;", result_var));
                    }
                }
            }
            if let Some(ref index_var) = cursor_info.index_var {
                if declared_cursor_idxs.insert(index_var.clone()) {
                    body_lines.push(format!("int {} = 0;", index_var));
                }
            }
        }

        let logic_text = proc.java_logic_lines.join(" ");
        let needs_found = logic_text.contains(" found ") || logic_text.contains("!found") || logic_text.contains("(found");
        if needs_found {
            body_lines.push("boolean found = false;".to_string());
        }
        if logic_text.contains("__SQLERRM__") {
            body_lines.push("String __SQLERRM__ = \"\";".to_string());
        }
        if logic_text.contains("__SQLCODE__") {
            body_lines.push("int __SQLCODE__ = 0;".to_string());
        }
        let needs_rowcount = logic_text.contains("__ROWCOUNT__");
        if needs_rowcount {
            body_lines.push("int __ROWCOUNT__ = 0;".to_string());
        }

        for line in &proc.java_logic_lines {
            let mut l = line.replace("mapper.", &format!("{}.", mapper_name));
            l = append_local_vars_to_mapper_calls(&l, proc, mapper_name, package_vars);
            let is_self_call = l.trim().starts_with("this.");
            for (vname, _inner_type) in &proc.out_local_vars {
                let vcamel = snake_to_camel(vname);
                if !is_self_call {
                    l = l.replace(&format!("{},", vcamel), &format!("{}.get(),", vcamel));
                    l = l.replace(&format!("{})", vcamel), &format!("{}.get())", vcamel));
                }
                l = l.replace(&format!("String.valueOf({})", vcamel), &format!("String.valueOf({}.get())", vcamel));
                let assign_pat = format!("{} = ", vcamel);
                if l.contains(&assign_pat) && !l.contains(&format!("{}.set(", vcamel)) {
                    l = l.replace(&assign_pat, &format!("{}.set(", vcamel));
                    if l.trim().ends_with(";") {
                        let trimmed = l.trim();
                        l = format!("{});", &trimmed[..trimmed.len()-1]);
                    }
                }
                l = l.replace(&format!("{} != null", vcamel), &format!("{}.get() != null", vcamel));
                l = l.replace(&format!("{} == null", vcamel), &format!("{}.get() == null", vcamel));
            }
            let trimmed = l.trim().to_string();
            if trimmed == "null;" || trimmed == "null" {
                l = format!("// {}", trimmed);
            } else if trimmed.starts_with("null /*") && trimmed.ends_with("*/;") {
                l = format!("// {}", trimmed);
            } else if trimmed.starts_with("/*") && trimmed.contains("null;") {
                l = l.replace("null;", "");
            }
            if needs_rowcount && l.contains(&format!("{}.", mapper_name)) && l.trim().ends_with(";") && !l.contains("=") && !l.contains("List<") && !l.contains("Map<") {
                l = l.replace(&format!("{}.", mapper_name), &format!("__ROWCOUNT__ = {}.", mapper_name));
            }
            body_lines.push(l);
        }

        for rc_out in &refcursor_outs {
            let rc_java = snake_to_camel(&rc_out.name);
            if let Some(cursor_info) = proc.open_cursors.get(&rc_java) {
                if let Some(ref result_var) = cursor_info.result_var {
                    body_lines.push(format!("if ({} != null) {{ return {}; }}", result_var, result_var));
                    body_lines.push("return java.util.Collections.emptyList();".to_string());
                }
            } else {
                body_lines.push(format!("return {}Result;", rc_java));
            }
        }

        if body_lines.is_empty() {
            body_lines.push("// Auto-generated from stored procedure".to_string());
            if proc.is_function {
                body_lines.push("return null;".to_string());
            }
        }

        if ret_type != "void" {
              let last_line = body_lines.last().map(|s| s.trim().to_string()).unwrap_or_default();
              let needs_fallback = if last_line.starts_with("return ") || last_line.starts_with("return;") {
                  false
              } else if last_line == "}" {
                  !is_if_else_all_return(&body_lines)
              } else {
                  true
              };
              if needs_fallback {
                 let fallback = if ret_type.contains("List") {
                     "return java.util.Collections.emptyList();"
                 } else if ret_type == "int" || ret_type == "Integer" {
                     "return 0;"
                 } else if ret_type == "long" || ret_type == "Long" {
                     "return 0L;"
                 } else if ret_type == "double" || ret_type == "Double" {
                     "return 0.0;"
                 } else if ret_type == "boolean" || ret_type == "Boolean" {
                     "return false;"
                 } else if ret_type.contains("BigDecimal") {
                      "return java.math.BigDecimal.ZERO;"
                  } else {
                      "return null;"
                  };
                  body_lines.push(fallback.to_string());
              }
          }
     }

    let mut result = Vec::new();
    let source_info = if !proc.source_file.is_empty() {
        format!(
            "{}:{}-{}",
            proc.source_file, proc.source_start_line, proc.source_end_line
        )
    } else {
        String::new()
    };
    result.push(format!(
        "    // Source: {} ({}) — {}",
        proc.name,
        if proc.is_function { "FUNCTION" } else { "PROCEDURE" },
        source_info
    ));
    // Add leading comments before the method
    for c in &proc.leading_comments {
        let formatted = format_comment_for_java(c);
        if !formatted.is_empty() {
            result.push(format!("    {}", formatted));
        }
    }
    if has_dml {
        result.push("    @Transactional".to_string());
    }
    result.push(format!("    public {} {}({}) {{", ret_type, method_name, params_str));
    // Add inline comments at heuristic positions (at start of body, simplified approach)
    for c in &proc.inline_comments {
        let formatted = format_comment_for_java(c);
        if !formatted.is_empty() {
            body_lines.insert(0, formatted);
        }
    }
    for line in &body_lines {
        result.push(format!("        {}", line));
    }
    result.push("    }".to_string());
    result.join("\n")
}

fn append_local_vars_to_mapper_calls(
    line: &str,
    proc: &crate::types::ProcedureInfo,
    mapper_name: &str,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
) -> String {
    let mapper_prefix = format!("{}.", mapper_name);
    if !line.contains(&mapper_prefix) {
        return line.to_string();
    }

    let param_java_names: std::collections::HashSet<String> = proc
        .parameters
        .iter()
        .filter(|p| !p.is_out())
        .map(|p| snake_to_camel(&p.name))
        .collect();

    let out_params: Vec<&crate::types::Parameter> = proc
        .parameters
        .iter()
        .filter(|p| p.is_out())
        .collect();

    let call_re = regex::Regex::new(&format!(
        r"{}\.(\w+)\s*\(([^)]*)\)",
        regex::escape(mapper_name)
    )).unwrap();

    let mut result = line.to_string();
    for caps in call_re.captures_iter(line) {
        let method_name = caps.get(1).unwrap().as_str();
        let existing_args = caps.get(2).unwrap().as_str().trim();

        let dml = proc.dml_statements.iter().find(|d| d.method_id == method_name);
        if let Some(dml) = dml {
            let mut promoted_extra: Vec<(String, String)> = dml.extra_params.clone();
            for p in out_params.iter() {
                let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&p.name))).unwrap();
                if re.is_match(&dml.sql_text) {
                    let jn = snake_to_camel(&p.name);
                    if !promoted_extra.iter().any(|(n, _)| n == &jn) {
                        let jt = proc.out_local_vars.get(&p.name.to_lowercase())
                            .cloned()
                            .unwrap_or_else(|| p.java_type.clone());
                        promoted_extra.push((jn, jt));
                    }
                }
            }

            let extra_param_names: std::collections::HashSet<String> = promoted_extra
                .iter()
                .map(|(name, _)| name.to_lowercase())
                .collect();

            let mut local_args: Vec<String> = Vec::new();
            let mut pkg_args: Vec<String> = Vec::new();
            let word_re = regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap();

            for word_caps in word_re.captures_iter(&dml.sql_text) {
                let word = word_caps.get(1).unwrap().as_str();
                if proc.local_vars.contains_key(&word.to_lowercase()) {
                    let jn = snake_to_camel(word);
                    let jn_lower = jn.to_lowercase();
                    if !param_java_names.iter().any(|pn| pn.to_lowercase() == jn_lower)
                        && !extra_param_names.contains(&jn_lower)
                    {
                        local_args.push(jn);
                    }
                }
            }
            local_args.sort();
            local_args.dedup();

            for word_caps in word_re.captures_iter(&dml.sql_text) {
                let word = word_caps.get(1).unwrap().as_str();
                if let Some(_) = package_vars.get(word) {
                    let jn = snake_to_camel(word);
                    let jn_lower = jn.to_lowercase();
                    if !param_java_names.iter().any(|pn| pn.to_lowercase() == jn_lower)
                        && !local_args.iter().any(|a| a.to_lowercase() == jn_lower)
                        && !extra_param_names.contains(&jn_lower)
                    {
                        pkg_args.push(jn);
                    }
                }
            }
            pkg_args.sort();
            pkg_args.dedup();

            let mut extra_args: Vec<String> = Vec::new();

            for (name, _) in &promoted_extra {
                let jn_lower = name.to_lowercase();
                if !param_java_names.iter().any(|pn| pn.to_lowercase() == jn_lower) {
                    if out_params.iter().any(|p| snake_to_camel(&p.name).to_lowercase() == jn_lower) {
                        extra_args.push(format!("{}.get()", name));
                    } else {
                        extra_args.push(name.clone());
                    }
                }
            }

            extra_args.extend(local_args);
            extra_args.extend(pkg_args);

            if !extra_args.is_empty() {
                let new_args = if existing_args.is_empty() {
                    extra_args.join(", ")
                } else {
                    format!("{}, {}", existing_args, extra_args.join(", "))
                };
                let old_call = format!("{}.{method_name}({existing_args})", mapper_name);
                let new_call = format!("{}.{method_name}({new_args})", mapper_name);
                result = result.replace(&old_call, &new_call);
            }
        }
    }
    result
}

pub fn collect_service_injections(pkg: &PackageInfo) -> std::collections::HashMap<String, String> {
    let own_svc = format!(
        "{}Service",
        {
            let cn = package_to_classname(&pkg.package_name);
            let mut c = cn.chars();
            match c.next() {
                Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                None => String::new(),
            }
        }
    );
    let system_svc_prefixes = ["DbeScheduler", "DbmsOutput", "DbmsRandom", "DbmsLob", "DbeOutput", "UtlFile", "DbmsSql", "DbmsJob"];
    let mut services = std::collections::HashMap::new();
    for proc in &pkg.procedures {
        for call in &proc.service_calls {
            if call.service_name == own_svc {
                continue;
            }
            if system_svc_prefixes.iter().any(|sp| call.service_name.starts_with(sp)) {
                continue;
            }
            if !services.contains_key(&call.service_name) {
                services.insert(call.service_name.clone(), call.package_name.clone());
            }
        }
    }
    services
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{DmlStatement, DmlType, Parameter, ProcedureInfo, ServiceCall};

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
    fn test_simple_service() {
        let proc = make_proc("do_stuff");
        let mut pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_class(dir.path(), &mut pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8, false).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/service/OrderService.java"),
        ).unwrap();
        assert!(content.contains("package com.example.demo.service;"));
        assert!(content.contains("@Service"));
        assert!(content.contains("public class OrderService"));
        assert!(content.contains("private static final Logger log"));
        assert!(content.contains("private final OrderMapper orderMapper;"));
    }

    #[test]
    fn test_function_return_type() {
        let mut proc = ProcedureInfo::new(
            "pkg_common.get_sys_date".to_string(),
            "pkg_common".to_string(),
            "get_sys_date".to_string(),
        );
        proc.is_function = true;
        proc.return_type = Some("timestamp".to_string());
        let mut pkg = make_pkg("pkg_common", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_class(dir.path(), &mut pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8, false).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/service/CommonService.java"),
        ).unwrap();
        assert!(content.contains("public java.sql.Timestamp getSysDate()"));
    }

    #[test]
    fn test_out_params_use_atomic_reference() {
        let mut proc = make_proc("get_data");
        proc.parameters.push(Parameter {
            name: "p_result".to_string(),
            java_type: "String".to_string(),
            sql_type: "varchar".to_string(),
            mode: Some(ParamMode::Out),
            default_value: None,
        });
        let mut pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_class(dir.path(), &mut pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8, false).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/service/DataService.java"),
        ).unwrap();
        assert!(content.contains("AtomicReference<String> pResult"));
        assert!(content.contains("import java.util.concurrent.atomic.AtomicReference;"));
    }

    #[test]
    fn test_service_injections() {
        let mut proc = make_proc("create_order");
        proc.service_calls.push(ServiceCall {
            service_name: "inventoryService".to_string(),
            method_name: "reserveStock".to_string(),
            args: vec!["productId".to_string()],
            package_name: "pkg_inventory".to_string(),
        });
        let mut pkg = make_pkg("pkg_order", vec![proc]);
        let injections = collect_service_injections(&pkg);
        assert_eq!(injections.get("inventoryService").unwrap(), "pkg_inventory");

        let dir = tempfile::tempdir().unwrap();
        write_service_class(dir.path(), &mut pkg, "com.example.demo", &injections, encoding_rs::UTF_8, false).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/service/OrderService.java"),
        ).unwrap();
        assert!(content.contains("private final InventoryService inventoryService;"));
        assert!(content.contains("import com.example.demo.service.InventoryService;"));
    }

    #[test]
    fn test_transactional_on_dml() {
        let mut proc = make_proc("create_order");
        proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Insert,
                    method_id: "insertOrder".to_string(),
                    sql_text: "insert into t values(1)".to_string(),
                    result_type: None,
                    ..Default::default()
                });
        let mut pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_class(dir.path(), &mut pkg, "com.example.demo", &Default::default(), encoding_rs::UTF_8, false).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/service/OrderService.java"),
        ).unwrap();
         assert!(content.contains("@Transactional"));
     }
 }

fn is_if_else_all_return(lines: &[String]) -> bool {
    let lines_trimmed: Vec<&str> = lines.iter().map(|l| l.trim()).collect();
    let n = lines_trimmed.len();
    if n < 3 { return false; }
    if lines_trimmed[n-1] != "}" { return false; }

    let mut depth: i32 = 0;
    let mut found_return_in_branch = false;
    let mut all_return = true;

    for i in (0..n).rev() {
        let line = lines_trimmed[i];
        for ch in line.chars() {
            match ch {
                '}' => depth += 1,
                '{' => depth -= 1,
                _ => {}
            }
        }
        if depth == 0 && (line.starts_with("if ") || line.starts_with("if(")) {
            return all_return && found_return_in_branch;
        }
        if depth == 0 && line.starts_with("}") && !line.contains("else") {
            break;
        }
        if line.starts_with("return ") || line == "return;" || line.starts_with("return;") {
            found_return_in_branch = true;
        }
        if line.starts_with("}") && line.contains("else") && !line.contains("if") {
            if !found_return_in_branch {
                all_return = false;
            }
            found_return_in_branch = false;
        }
    }
    false
}
