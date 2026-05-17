CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

DROP SCHEMA IF EXISTS nimbusbd CASCADE;

CREATE SCHEMA nimbusbd;

SET search_path TO nimbusbd, public;

-- ============================================================
--  Cloud Group 5 — Esquema PostgreSQL
--  Ejecutar en orden: los tipos ENUM primero, luego las tablas
--  en la secuencia que aparecen (respeta dependencias de FK).
-- ============================================================

-- ─────────────────────────────────────────
--  TIPOS ENUM
-- ─────────────────────────────────────────

CREATE TYPE user_role         AS ENUM ('admin', 'mentor', 'participante');
CREATE TYPE backend_type      AS ENUM ('openstack', 'linux_cluster');
CREATE TYPE cluster_type      AS ENUM ('linux', 'openstack');
CREATE TYPE node_type         AS ENUM ('server', 'pc', 'router', 'switch');
CREATE TYPE node_state        AS ENUM ('pending', 'running', 'stopped', 'error');
CREATE TYPE physical_status   AS ENUM ('online', 'offline', 'maintenance');
CREATE TYPE slice_status      AS ENUM ('pending', 'provisioning', 'running',
                                       'stopping', 'stopped', 'error', 'deleted');
CREATE TYPE slice_permission  AS ENUM ('owner', 'viewer');
CREATE TYPE vm_status         AS ENUM ('running', 'stopped', 'error');
CREATE TYPE slice_event_type  AS ENUM ('created', 'started', 'stopped',
                                       'restarted', 'error', 'deleted');

-- ─────────────────────────────────────────
--  MÓDULO: AUTH / USUARIOS
-- ─────────────────────────────────────────

CREATE TABLE users (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(120)    NOT NULL,
    email           VARCHAR(255)    NOT NULL UNIQUE,
    password_hash   VARCHAR(255)    NOT NULL,
    role            user_role       NOT NULL DEFAULT 'participante',
    active          BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT        NOT NULL UNIQUE,
    ip_address  INET,
    user_agent  TEXT,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE audit_log (
    id              BIGSERIAL   PRIMARY KEY,
    user_id         UUID        REFERENCES users(id) ON DELETE SET NULL,
    action          VARCHAR(80) NOT NULL,       -- ej. 'slice.create', 'user.login'
    resource_type   VARCHAR(60),                -- ej. 'slice', 'course', 'user'
    resource_id     UUID,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
--  MÓDULO: CURSOS
-- ─────────────────────────────────────────

CREATE TABLE courses (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(20) NOT NULL UNIQUE,    -- ej. 'TEL141'
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    mentor_id       UUID        REFERENCES users(id) ON DELETE SET NULL,
    active          BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE course_enrollments (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id   UUID        NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    user_id     UUID        NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    UNIQUE (course_id, user_id)     -- un usuario no se inscribe dos veces
);

-- ─────────────────────────────────────────
--  MÓDULO: IMÁGENES DE VM
--  (debe existir antes de vm_configs)
-- ─────────────────────────────────────────

CREATE TABLE vm_images (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(120)    NOT NULL UNIQUE,     -- ej. 'Ubuntu 22.04 LTS'
    os_family   VARCHAR(60),                         -- ej. 'Ubuntu', 'Debian'
    version     VARCHAR(40),
    backend_ref VARCHAR(200),                        -- ID/path en OpenStack o Linux cluster
    backend     backend_type    NOT NULL,
    active      BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
--  MÓDULO: NODOS FÍSICOS (infraestructura)
--  (debe existir antes de slices y placement)
-- ─────────────────────────────────────────

CREATE TABLE physical_nodes (
    id           UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    hostname     VARCHAR(120)    NOT NULL UNIQUE,
    ip_address   VARCHAR(45)     NOT NULL,
    cluster_type cluster_type    NOT NULL,
    status       physical_status NOT NULL DEFAULT 'online',
    cpu_cores    REAL            NOT NULL,
    ram_mb       BIGINT          NOT NULL,
    disk_gb      BIGINT          NOT NULL,
    last_seen    TIMESTAMPTZ,
    created_at   TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
--  MÓDULO: BIBLIOTECA DE SLICES (borradores)
-- ─────────────────────────────────────────

CREATE TABLE slice_templates (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(200) NOT NULL,
    description         TEXT,
    created_by          UUID        REFERENCES users(id) ON DELETE SET NULL,
    -- Definición lógica completa: nodos, enlaces, configs.
    -- El frontend y el Slice Manager leen/escriben aquí.
    topology_definition JSONB       NOT NULL DEFAULT '{}',
    vm_count            INTEGER     NOT NULL DEFAULT 0,
    link_count          INTEGER     NOT NULL DEFAULT 0,
    active              BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
--  MÓDULO: SLICES DESPLEGADOS
-- ─────────────────────────────────────────

CREATE TABLE slices (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(200)    NOT NULL,
    course_id           UUID            REFERENCES courses(id)        ON DELETE SET NULL,
    template_id         UUID            REFERENCES slice_templates(id) ON DELETE SET NULL,
    owner_id            UUID            REFERENCES users(id)          ON DELETE SET NULL,
    status              slice_status    NOT NULL DEFAULT 'pending',
    availability_zone   VARCHAR(60),                -- ej. 'us-east-1a'
    physical_node_id    UUID            REFERENCES physical_nodes(id) ON DELETE SET NULL,
    placement_metadata  JSONB,                      -- snapshot de la decisión de placement
    started_at          TIMESTAMPTZ,
    stopped_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE slice_access (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    slice_id    UUID            NOT NULL REFERENCES slices(id) ON DELETE CASCADE,
    user_id     UUID            NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    permission  slice_permission NOT NULL DEFAULT 'viewer',
    granted_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (slice_id, user_id)
);

-- ─────────────────────────────────────────
--  MÓDULO: TOPOLOGÍA
-- ─────────────────────────────────────────

CREATE TABLE topology_nodes (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slice_id                UUID        NOT NULL REFERENCES slices(id) ON DELETE CASCADE,
    label                   VARCHAR(80) NOT NULL,
    node_type               node_type   NOT NULL DEFAULT 'server',
    openstack_instance_id   VARCHAR(120),   -- ID en OpenStack (si aplica)
    linux_vm_id             VARCHAR(120),   -- ID en Linux cluster (si aplica)
    state                   node_state  NOT NULL DEFAULT 'pending',
    pos_x                   REAL,           -- posición en el canvas del frontend
    pos_y                   REAL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE topology_links (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slice_id        UUID        NOT NULL REFERENCES slices(id)         ON DELETE CASCADE,
    source_node_id  UUID        NOT NULL REFERENCES topology_nodes(id) ON DELETE CASCADE,
    target_node_id  UUID        NOT NULL REFERENCES topology_nodes(id) ON DELETE CASCADE,
    vlan_id         VARCHAR(20),
    network_name    VARCHAR(120),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Evitar enlaces duplicados en la misma dirección
    UNIQUE (slice_id, source_node_id, target_node_id)
);

CREATE TABLE vm_configs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id         UUID        NOT NULL UNIQUE REFERENCES topology_nodes(id) ON DELETE CASCADE,
    flavor          VARCHAR(60),            -- ej. 'm1.medium'
    disk_gb         INTEGER,
    image_id        UUID        REFERENCES vm_images(id) ON DELETE SET NULL,
    keypair_name    VARCHAR(80),
    -- Reglas de seguridad: array de objetos {proto, port, cidr}
    security_rules  JSONB       NOT NULL DEFAULT '[]',
    extra_config    JSONB       NOT NULL DEFAULT '{}',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
--  MÓDULO: VM PLACEMENT
-- ─────────────────────────────────────────

CREATE TABLE placement_decisions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slice_id            UUID        NOT NULL REFERENCES slices(id)         ON DELETE CASCADE,
    vm_node_id          UUID        NOT NULL REFERENCES topology_nodes(id) ON DELETE CASCADE,
    physical_node_id    UUID        NOT NULL REFERENCES physical_nodes(id) ON DELETE CASCADE,
    algorithm_used      VARCHAR(40),    -- ej. 'best-fit', 'round-robin', 'affinity'
    score               REAL,
    -- Snapshot de métricas del nodo en el momento de la decisión
    resource_snapshot   JSONB,
    decided_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
--  MÓDULO: MONITOREO (estado actual)
-- ─────────────────────────────────────────

-- Una fila por VM. El agente/Prometheus hace UPSERT cada scrape.
CREATE TABLE vm_metrics_current (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id         UUID        NOT NULL UNIQUE REFERENCES topology_nodes(id) ON DELETE CASCADE,
    cpu_pct         REAL,
    ram_pct         REAL,
    net_in_mbps     REAL,
    net_out_mbps    REAL,
    disk_used_pct   REAL,
    vm_status       vm_status,
    sampled_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Una fila por nodo físico. Idem UPSERT.
CREATE TABLE physical_metrics_current (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    physical_node_id    UUID        NOT NULL UNIQUE REFERENCES physical_nodes(id) ON DELETE CASCADE,
    cpu_pct             REAL,
    ram_pct             REAL,
    net_in_mbps         REAL,
    disk_used_pct       REAL,
    active_vms          INTEGER     NOT NULL DEFAULT 0,
    sampled_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
--  MÓDULO: EVENTOS DE SLICE
-- ─────────────────────────────────────────

CREATE TABLE slice_events (
    id              BIGSERIAL           PRIMARY KEY,
    slice_id        UUID                NOT NULL REFERENCES slices(id) ON DELETE CASCADE,
    triggered_by    UUID                REFERENCES users(id) ON DELETE SET NULL,
    event_type      slice_event_type    NOT NULL,
    message         TEXT,
    payload         JSONB,
    occurred_at     TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

-- ============================================================
--  ÍNDICES
--  Solo los más importantes para las queries frecuentes.
-- ============================================================

-- Auth
CREATE INDEX idx_sessions_user_id         ON sessions(user_id);
CREATE INDEX idx_sessions_expires_at      ON sessions(expires_at);
CREATE INDEX idx_audit_log_user_id        ON audit_log(user_id);
CREATE INDEX idx_audit_log_resource       ON audit_log(resource_type, resource_id);
CREATE INDEX idx_audit_log_created_at     ON audit_log(created_at DESC);

-- Cursos
CREATE INDEX idx_courses_mentor_id        ON courses(mentor_id);
CREATE INDEX idx_enrollments_user_id      ON course_enrollments(user_id);
CREATE INDEX idx_enrollments_course_id    ON course_enrollments(course_id);

-- Slices
CREATE INDEX idx_slices_course_id         ON slices(course_id);
CREATE INDEX idx_slices_owner_id          ON slices(owner_id);
CREATE INDEX idx_slices_status            ON slices(status);
CREATE INDEX idx_slices_physical_node     ON slices(physical_node_id);
CREATE INDEX idx_slice_access_user_id     ON slice_access(user_id);

-- Topología
CREATE INDEX idx_topo_nodes_slice_id      ON topology_nodes(slice_id);
CREATE INDEX idx_topo_links_slice_id      ON topology_links(slice_id);
CREATE INDEX idx_topo_links_source        ON topology_links(source_node_id);
CREATE INDEX idx_topo_links_target        ON topology_links(target_node_id);
CREATE INDEX idx_vm_configs_node_id       ON vm_configs(node_id);

-- Placement y monitoreo
CREATE INDEX idx_placement_slice_id       ON placement_decisions(slice_id);
CREATE INDEX idx_placement_physical_node  ON placement_decisions(physical_node_id);
CREATE INDEX idx_vm_metrics_node_id       ON vm_metrics_current(node_id);
CREATE INDEX idx_phys_metrics_node_id     ON physical_metrics_current(physical_node_id);

-- Eventos
CREATE INDEX idx_slice_events_slice_id    ON slice_events(slice_id);
CREATE INDEX idx_slice_events_occurred_at ON slice_events(occurred_at DESC);

-- ============================================================
--  TRIGGER: updated_at automático
--  Aplica a todas las tablas que tienen esa columna.
-- ============================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_courses_updated_at
    BEFORE UPDATE ON courses
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_slice_templates_updated_at
    BEFORE UPDATE ON slice_templates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_slices_updated_at
    BEFORE UPDATE ON slices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_vm_configs_updated_at
    BEFORE UPDATE ON vm_configs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
