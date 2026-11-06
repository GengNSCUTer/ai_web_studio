"""记忆派生索引及事务内修订号；迁移和隔离测试共用相同数据库定义。"""

from sqlalchemy import inspect, text


ANN_DIMENSIONS = (256, 384, 512, 768, 1024, 1536, 2048, 3072)

PREPARE_SQL = r"""
CREATE OR REPLACE FUNCTION aiws_memory_prepare_search() RETURNS trigger AS $$
DECLARE
    part text;
    tokens text[] := ARRAY[]::text[];
    i integer;
BEGIN
    FOR part IN SELECT m[1] FROM regexp_matches(lower(coalesce(NEW.title, '') || E'\n' ||
        coalesce(NEW.content, '')), '[a-z0-9_]{2,}|[一-龥]+', 'g') AS m LOOP
        IF part ~ '^[一-龥]+$' AND length(part) > 1 THEN
            FOR i IN 1..length(part)-1 LOOP
                tokens := array_append(tokens, substr(part, i, 2));
            END LOOP;
        ELSE
            tokens := array_append(tokens, part);
        END IF;
    END LOOP;
    NEW.search_terms := array_to_string(tokens, ' ');
    NEW.search_embedding := NULL;
    NEW.search_dimensions := NULL;
    IF NEW.embedding_vector IS NOT NULL AND NEW.embedding_signature IS NOT NULL AND
       NEW.embedding_text_hash = encode(sha256(convert_to(NEW.version::text || ':' ||
           coalesce(NEW.title, '') || E'\n' || coalesce(NEW.content, ''), 'UTF8')), 'hex') THEN
        BEGIN
            NEW.search_embedding := NEW.embedding_vector::vector;
            IF vector_norm(NEW.search_embedding) > 0 THEN
                NEW.search_dimensions := vector_dims(NEW.search_embedding);
            ELSE
                NEW.search_embedding := NULL;
            END IF;
        EXCEPTION WHEN data_exception THEN
            NEW.search_embedding := NULL;
            NEW.search_dimensions := NULL;
        END;
    END IF;
    IF TG_OP = 'UPDATE' AND (OLD.title IS DISTINCT FROM NEW.title OR
        OLD.content IS DISTINCT FROM NEW.content OR OLD.version IS DISTINCT FROM NEW.version) THEN
        NEW.embedding_retry_after := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

REVISION_SQL = """
CREATE OR REPLACE FUNCTION aiws_memory_bump_revision() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND
       (to_jsonb(OLD) - ARRAY['embedding_vector','embedding_signature','embedding_text_hash',
           'search_terms','search_vector','search_embedding','search_dimensions','embedding_retry_after']) =
       (to_jsonb(NEW) - ARRAY['embedding_vector','embedding_signature','embedding_text_hash',
           'search_terms','search_vector','search_embedding','search_dimensions','embedding_retry_after']) THEN
        RETURN NULL;
    END IF;
    IF TG_OP <> 'INSERT' THEN
        UPDATE users SET memory_revision = memory_revision + 1 WHERE id = OLD.user_id;
    END IF;
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.user_id IS DISTINCT FROM OLD.user_id) THEN
        UPDATE users SET memory_revision = memory_revision + 1 WHERE id = NEW.user_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

SCOPE_SQL = """
CREATE OR REPLACE FUNCTION aiws_memory_scope_revision() RETURNS trigger AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        UPDATE users SET memory_revision = memory_revision + 1 WHERE id = OLD.user_id;
    END IF;
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.user_id IS DISTINCT FROM OLD.user_id) THEN
        UPDATE users SET memory_revision = memory_revision + 1 WHERE id = NEW.user_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

SETTINGS_SQL = """
CREATE OR REPLACE FUNCTION aiws_memory_reset_model_index() RETURNS trigger AS $$
BEGIN
    IF (OLD.knowledge_embedding_provider, OLD.knowledge_embedding_model,
        OLD.knowledge_embedding_dimensions, OLD.knowledge_embedding_base_url) IS DISTINCT FROM
       (NEW.knowledge_embedding_provider, NEW.knowledge_embedding_model,
        NEW.knowledge_embedding_dimensions, NEW.knowledge_embedding_base_url) THEN
        UPDATE user_memories SET search_embedding = NULL, search_dimensions = NULL,
            embedding_retry_after = NULL WHERE user_id = NEW.user_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def install_memory_search(connection):
    """仅在 PostgreSQL 安装，SQLite 保留既有业务测试路径。"""
    if connection.dialect.name != "postgresql":
        return
    for statement in (PREPARE_SQL, REVISION_SQL, SCOPE_SQL, SETTINGS_SQL):
        connection.execute(text(statement))
    triggers = {
        "user_memories": [
            ("aiws_memory_prepare", "BEFORE INSERT OR UPDATE OF title, content, version, embedding_vector, embedding_signature, embedding_text_hash", "aiws_memory_prepare_search()"),
            ("aiws_memory_revision", "AFTER INSERT OR UPDATE OR DELETE", "aiws_memory_bump_revision()"),
        ],
        "conversations": [
            ("aiws_memory_source_move", "AFTER UPDATE OF user_id, project_id", "aiws_memory_scope_revision()"),
            ("aiws_memory_source_delete", "AFTER DELETE", "aiws_memory_scope_revision()"),
        ],
        "projects": [
            ("aiws_memory_project_move", "AFTER UPDATE OF user_id", "aiws_memory_scope_revision()"),
            ("aiws_memory_project_delete", "AFTER DELETE", "aiws_memory_scope_revision()"),
        ],
        "user_settings": [
            ("aiws_memory_model", "AFTER UPDATE OF knowledge_embedding_provider, knowledge_embedding_model, knowledge_embedding_dimensions, knowledge_embedding_base_url", "aiws_memory_reset_model_index()"),
        ],
    }
    tables = set(inspect(connection).get_table_names())
    for table, definitions in triggers.items():
        if table not in tables:
            continue
        for name, timing, function in definitions:
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
            connection.execute(text(f"CREATE TRIGGER {name} {timing} ON {table} "
                                    f"FOR EACH ROW EXECUTE FUNCTION {function}"))


def after_create(metadata, connection, **kwargs):
    if connection.dialect.name == "postgresql" and "user_memories" in inspect(connection).get_table_names():
        install_memory_search(connection)
