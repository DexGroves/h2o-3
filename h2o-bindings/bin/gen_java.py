#!/usr/bin/env python
# -*- encoding: utf-8 -*-
from __future__ import unicode_literals
from builtins import range
import re
import bindings as bi


class JavaTypeTranslator(bi.TypeTranslator):
    def __init__(self):
        bi.TypeTranslator.__init__(self)
        self.types["string"] = "String"


def translate_type(h2o_type, schema):
    return type_adapter.translate(h2o_type, schema)


def get_java_value(field):
    value = field["value"]
    h2o_type = field["type"]
    java_type = translate_type(h2o_type, field["schema_name"])

    if java_type == "float" and value == "Infinity": return "Float.POSITIVE_INFINITY"
    if java_type == "double" and value == "Infinity": return "Double.POSITIVE_INFINITY"
    if java_type == "long": return str(value) + "L"
    if java_type == "float": return str(value) + "f"
    if java_type == "boolean": return str(value).lower()
    if java_type == "String" and (value == "" or value is None): return '""'
    if java_type == "String": return '"%s"' % value
    if value is None: return "null"
    if h2o_type.startswith("enum"): return field["schema_name"] + "." + value
    if h2o_type.endswith("[][]"): return "null"  # TODO
    if h2o_type.endswith("[]"):
        basetype = field["schema_name"] if field["is_schema"] else h2o_type.partition("[")[0]
        if basetype == "Iced": basetype = "Object"
        return "new %s[]{%s}" % (basetype, str(value)[1:-1])
    if h2o_type.startswith("Map"): return "null"  # TODO: handle Map
    if h2o_type.startswith("Key"): return "null"  # TODO: handle Key
    return value

def translate_name(name):
    """
    Converts names with underscores into camelcase. For example:
        "num_rows" => "numRows"
        "very_long_json_name" => "veryLongJsonName"
        "build_GBM_model" => "buildGbmModel"
        "KEY" => "key"
    """
    parts = name.split("_")
    parts[0] = parts[0].lower()
    for i in range(1, len(parts)):
        parts[i] = parts[i].capitalize()
    return "".join(parts)


# -----------------------------------------------------------------------------------------------------------------------
# Generate Schema POJOs
# -----------------------------------------------------------------------------------------------------------------------
def generate_schema(class_name, schema):
    """
    Generate schema POJO file.
      :param class_name: name of the class
      :param schema: information about the class
    """
    has_map = False
    is_model_builder = False
    has_inherited = False
    for field in schema["fields"]:
        if field["name"] == "__meta": continue
        if field["is_inherited"]:
            has_inherited = True
            continue
        if field["type"].startswith("Map"): has_map = True
        if field["name"] == "can_build": is_model_builder = True

    superclass = schema["superclass"]
    if superclass == "Iced": superclass = "Object"

    fields = []
    for field in schema["fields"]:
        if field["name"] == "__meta": continue
        java_type = translate_type(field["type"], field["schema_name"])
        java_value = get_java_value(field)

        # hackery: we flatten the parameters up into the ModelBuilder schema, rather than nesting them in the
        # parameters schema class...
        if is_model_builder and field["name"] == "parameters":
            fields.append(("parameters", "null", "ModelParameterSchemaV3[]", field["help"], field["is_inherited"]))
        else:
            fields.append((field["name"], java_value, java_type, field["help"], field["is_inherited"]))

    yield "/**"
    yield " * This file is auto-generated by h2o-3/h2o-bindings/bin/gen_java.py"
    yield " * Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details)"
    yield " */"
    yield "package water.bindings.pojos;"
    yield ""
    yield "import com.google.gson.Gson;"
    yield "import com.google.gson.annotations.*;"
    yield "import java.util.Map;" if has_map else None
    yield ""
    yield ""
    yield "public class %s extends %s {" % (class_name, superclass)
    yield ""
    for name, value, ftype, fhelp, inherited in fields:
        if inherited: continue
        ccname = translate_name(name)
        yield "    /**"
        yield bi.wrap(fhelp, indent="     * ")
        yield "     */"
        yield "    @SerializedName(\"%s\")" % name  if name != ccname else None
        yield "    public %s %s;" % (ftype, ccname)
        yield ""
    if has_inherited:
        yield ""
        yield "    /*" + ("-" * 114)
        yield "    //" + (" " * 50) + "INHERITED"
        yield "    //" + ("-" * 114)
        yield ""
        for name, value, ftype, fhelp, inherited in fields:
            if not inherited: continue
            yield bi.wrap(fhelp, "    // ")
            yield "    public %s %s;" % (ftype, translate_name(name))
            yield ""
        yield "    */"
        yield ""
    yield "    /**"
    yield "     * Public constructor"
    yield "     */"
    yield "    public %s() {" % class_name
    for name, value, _, _, _ in fields:
        if name == "parameters": continue
        if value == "null": continue
        yield "        %s = %s;" % (translate_name(name), value)
    yield "    }"
    yield ""
    yield "    /**"
    yield "     * Return the contents of this object as a JSON String."
    yield "     */"
    yield "    @Override"
    yield "    public String toString() {"
    yield "        return new Gson().toJson(this);"
    yield "    }"
    yield ""
    yield "}"


# -----------------------------------------------------------------------------------------------------------------------
# Generate Enum classes
# -----------------------------------------------------------------------------------------------------------------------
def generate_enum(name, values):
    yield "/**"
    yield " * This file is auto-generated by h2o-3/h2o-bindings/bin/gen_java.py"
    yield " * Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details)"
    yield " */"
    yield "package water.bindings.pojos;"
    yield ""
    yield "public enum " + name + " {"
    for value in values:
        yield "    %s," % value
    yield "}"


# -----------------------------------------------------------------------------------------------------------------------
#  Generate Retrofit proxy classes
# -----------------------------------------------------------------------------------------------------------------------
def generate_proxy(classname, endpoints):
    """
    Retrofit interfaces look like this:
        public interface GitHubService {
            @GET("/users/{user}/repos")
            Call<List<Repo>> listRepos(@Path("user") String user);
        }
      :param classname: name of the class
      :param endpoints: list of endpoints served by this class
    """

    # Replace path vars like (?<schemaname>.*) with {schemaname} for Retrofit's annotation
    var_pattern = re.compile(r"\{(\w+)\}")

    helper_class = []
    found_key_array_parameter = False

    yield "/**"
    yield " * This file is auto-generated by h2o-3/h2o-bindings/bin/gen_java.py"
    yield " * Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details)"
    yield " */"
    yield "package water.bindings.proxies.retrofit;"
    yield ""
    yield "import water.bindings.pojos.*;"
    yield "import retrofit2.*;"
    yield "import retrofit2.http.*;"
    yield "import java.util.Map;"
    yield ""
    yield "public interface " + classname + " {"
    yield ""

    for e in endpoints:
        method = e["handler_method"]

        param_strs = []
        for field in e["input_params"]:
            fname = field["name"]
            ftype = "Path" if field["is_path_param"] else "Field"
            ptype = translate_type(field["type"], field["schema_name"])
            if ptype.endswith("KeyV3") or ptype == "ColSpecifierV3": ptype = "String"
            if ptype.endswith("KeyV3[]"): ptype = "String[]"
            param_strs.append("@{ftype}(\"{fname}\") {ptype} {fname}".format(**locals()))

        yield u"  /** "
        yield bi.wrap(e["summary"], indent="   * ")
        yield u"   */"
        yield u"  @FormUrlEncoded" if e["http_method"] == "POST" else None
        yield u"  @{method}(\"{path}\")".format(method=e["http_method"], path=e["url_pattern"])
        if len(param_strs) <= 1:
            args = param_strs[0] if param_strs else ""
            yield "  Call<{schema}> {method}({args});".format(schema=e["output_schema"], method=method, args=args)
        else:
            yield "  Call<{schema}> {method}(".format(schema=e["output_schema"], method=method)
            for arg in param_strs:
                yield "    " + arg + ("" if arg == param_strs[-1] else ",")
            yield "  );"
        yield ""

        # Make special static Helper class for Grid and ModelBuilders.
        if "algo" in e:
            # We make two train_ and validate_ methods.  One (built here) takes the parameters schema, the other
            # (built above) takes each parameter.
            helper_class.append("    /**")
            helper_class.append(bi.wrap(e["summary"], indent="     * "))
            helper_class.append("     */")
            helper_class.append("    public static Call<{oschema}> {method}({outer_class} z, {ischema} p) {{"
                                .format(ischema=e["input_schema"], oschema=e["output_schema"], method=method,
                                        outer_class=classname))
            helper_class.append("      return z.{method}(".format(method=method))
            for field in e["input_params"]:
                ptype = translate_type(field["type"], field["schema_name"])
                pname = translate_name(field["name"])
                if ptype.endswith("KeyV3"):
                    s = "(p.{parm} == null? null : p.{parm}.name)".format(parm=pname)
                elif ptype.endswith("KeyV3[]"):
                    found_key_array_parameter = True
                    s = "(p.{parm} == null? null : keyArrayToStringArray(p.{parm}))".format(parm=pname)
                elif ptype.startswith("ColSpecifier"):
                    s = "(p.{parm} == null? null : p.{parm}.columnName)".format(parm=pname)
                else:
                    s = "p." + pname
                if field != e["input_params"][-1]:
                    s += ","
                helper_class.append("        " + s)
            helper_class.append("      );")
            helper_class.append("    }")
            helper_class.append("")

    if helper_class:
        yield ""
        yield "  public static class Helper {"
        for line in helper_class:
            yield line
        if found_key_array_parameter:
            yield "    /**"
            yield "     * Return an array of Strings for an array of keys."
            yield "     */"
            yield "    public static String[] keyArrayToStringArray(KeyV3[] keys) {"
            yield "      if (keys == null) return null;"
            yield "      String[] ids = new String[keys.length];"
            yield "      int i = 0;"
            yield "      for (KeyV3 key : keys) ids[i++] = key.name;"
            yield "      return ids;"
            yield "    }"
        yield "  }"
        yield ""

    yield "}"


# -----------------------------------------------------------------------------------------------------------------------
#  Generate main Retrofit interface class
# -----------------------------------------------------------------------------------------------------------------------
def generate_main_class(endpoints, schemas_map):
    yield "/**"
    yield " * This file is auto-generated by h2o-3/h2o-bindings/bin/gen_java.py"
    yield " * Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details)"
    yield " */"
    yield "package water.bindings;"
    yield ""
    yield "import water.bindings.pojos.*;"
    yield "import water.bindings.proxies.retrofit.*;"
    yield "import retrofit2.*;"
    yield "import retrofit2.converter.gson.GsonConverterFactory;"
    yield "import com.google.gson.*;"
    yield "import okhttp3.OkHttpClient;"
    yield "import java.io.IOException;"
    yield "import java.lang.reflect.Type;"
    yield "import java.util.concurrent.TimeUnit;"
    yield ""
    yield "public class H2oApi {"
    yield ""
    yield "  public H2oApi() {}"
    yield "  public H2oApi(String url) { this.url = url; }"
    yield ""
    yield "  public void setUrl(String s) {"
    yield "    url = s;"
    yield "    retrofit = null;"
    yield "  }"
    yield ""
    yield "  public void setTimeout(int t) {"
    yield "    timeout_s = t;"
    yield "    retrofit = null;"
    yield "  }"
    yield ""

    for route in endpoints:
        newname = route["api_name"]
        class_name = route["class_name"]
        input_schema_name = route["input_schema"]
        output_schema_name = route["output_schema"]
        input_schema = schemas_map[input_schema_name]
        input_fields = [field  for field in input_schema["fields"]
                               if field["direction"] != "OUTPUT"]
        good_input_fields = [field  for field in input_fields if field["name"] != "_exclude_fields"]
        yield "  /**"
        yield bi.wrap(route["summary"], indent="   * ")
        yield "   */"
        if len(good_input_fields) == 0:
            yield "  public {type} {method}() throws IOException {{".\
                  format(type=output_schema_name, method=newname)
            yield "    return {method}(\"\");".format(method=newname)
            yield "  }"
            yield "  public {type} {method}(String[] excluded_fields) throws IOException {{".\
                  format(type=output_schema_name, method=newname)
            yield "    return {method}(String.join(\",\", excluded_fields));".format(method=newname)
            yield "  }"
            yield "  public {type} {method}(String excluded_fields) throws IOException {{".\
                  format(type=output_schema_name, method=newname)
            yield "    {clazz} s = getRetrofit().create({clazz}.class);".format(clazz=class_name)
            yield "    return s.{method}(excluded_fields).execute().body();".format(method=route["handler_method"])
            yield "  }"
            yield ""
        # elif len(good_input_fields) <= 2:
        #
        else:
            yield "  // %s -> %d fields: %s" % (newname, len(input_fields), [f["name"] for f in input_fields])
            #yield "  public {outtype} {method}();".format(outtype=output_schema_name, method=newname)
            yield ""

    yield ""
    yield "  //--------- PRIVATE ----------------------------------------------------------"
    yield ""
    yield "  private Retrofit retrofit;"
    yield "  private String url = \"http://localhost/54321/\";"
    yield "  private int timeout_s = 60;"
    yield ""
    yield "  private void initializeRetrofit() {"
    yield "    Gson gson = new GsonBuilder()"
    yield "      .registerTypeAdapter(KeyV3.class, new KeySerializer())"
    yield "      .create();"
    yield ""
    yield "    OkHttpClient client = new OkHttpClient.Builder()"
    yield "      .connectTimeout(timeout_s, TimeUnit.SECONDS)"
    yield "      .writeTimeout(timeout_s, TimeUnit.SECONDS)"
    yield "      .readTimeout(timeout_s, TimeUnit.SECONDS)"
    yield "      .build();"
    yield ""
    yield "    this.retrofit = new Retrofit.Builder()"
    yield "      .client(client)"
    yield "      .baseUrl(url)"
    yield "      .addConverterFactory(GsonConverterFactory.create(gson))"
    yield "      .build();"
    yield "  }"
    yield ""
    yield "  private Retrofit getRetrofit() {"
    yield "    if (retrofit == null) initializeRetrofit();"
    yield "    return retrofit;"
    yield "  }"
    yield ""
    yield ""
    yield "  /**"
    yield "   * Keys get sent as Strings and returned as objects also containing the type and URL,"
    yield "   * so they need a custom GSON serializer."
    yield "   */"
    yield "  private static class KeySerializer implements JsonSerializer<KeyV3> {"
    yield "    public JsonElement serialize(KeyV3 key, Type typeOfKey, JsonSerializationContext context) {"
    yield "      return new JsonPrimitive(key.name);"
    yield "    }"
    yield "  }"
    yield "}"


# -----------------------------------------------------------------------------------------------------------------------
# MAIN:
# -----------------------------------------------------------------------------------------------------------------------
def main():
    bi.init("Java", "java")

    for schema in bi.schemas():
        name = schema["name"]
        bi.vprint("Generating schema: " + name)
        bi.write_to_file("water/bindings/pojos/%s.java" % name, generate_schema(name, schema))

    for name, values in bi.enums().items():
        bi.vprint("Generating enum: " + name)
        bi.write_to_file("water/bindings/pojos/%s.java" % name, generate_enum(name, sorted(values)))

    sm = bi.schemas_map()
    for name, endpoints in bi.endpoint_groups().items():
        bi.vprint("Generating proxy: " + name)
        bi.write_to_file("water/bindings/proxies/retrofit/%s.java" % name, generate_proxy(name, endpoints))

    bi.vprint("Generating H2oApi.java")
    # bi.write_to_file("water/bindings/H2oApi.java", generate_main_class(bi.endpoints(), sm))

    type_adapter.vprint_translation_map()


if __name__ == "__main__":
    type_adapter = JavaTypeTranslator()
    main()
