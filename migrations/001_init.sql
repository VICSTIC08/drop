-- A drop is one sale: a fixed number of units and the strategy used to reserve them.
CREATE TABLE drops (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL,
    total_units  integer NOT NULL CHECK (total_units > 0),
    strategy     text NOT NULL CHECK (
        strategy IN ('naive', 'row_lock', 'atomic_update', 'redis_counter', 'unit_rows')
    ),
    hold_seconds integer NOT NULL DEFAULT 120 CHECK (hold_seconds > 0),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per drop holding the single "how many are left" number.
-- Used by the counter-based strategies (naive, row_lock, atomic_update).
CREATE TABLE inventory_counters (
    drop_id   uuid PRIMARY KEY REFERENCES drops (id) ON DELETE CASCADE,
    available integer NOT NULL
);

-- A hold is one buyer's temporary claim on one unit.
CREATE TABLE holds (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    drop_id    uuid NOT NULL REFERENCES drops (id) ON DELETE CASCADE,
    user_id    uuid NOT NULL,
    status     text NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'confirmed', 'expired', 'released')
    ),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX holds_drop_status_idx ON holds (drop_id, status);
