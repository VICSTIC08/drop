-- One row per sellable unit, used by the unit_rows strategy.
-- A unit is free while hold_id is NULL. Buyers each lock a DIFFERENT free row
-- (FOR UPDATE SKIP LOCKED), so nobody queues behind a single hot row.
CREATE TABLE inventory_units (
    drop_id uuid NOT NULL REFERENCES drops (id) ON DELETE CASCADE,
    unit_no integer NOT NULL,
    hold_id uuid REFERENCES holds (id) ON DELETE SET NULL,
    PRIMARY KEY (drop_id, unit_no)
);

-- Partial index: only the free units, so "find me a free unit" stays fast
-- even when almost everything is sold.
CREATE INDEX inventory_units_free_idx ON inventory_units (drop_id) WHERE hold_id IS NULL;

CREATE UNIQUE INDEX inventory_units_hold_idx ON inventory_units (hold_id) WHERE hold_id IS NOT NULL;

ALTER TABLE drops DROP CONSTRAINT drops_strategy_check;
ALTER TABLE drops ADD CONSTRAINT drops_strategy_check CHECK (
    strategy IN ('naive', 'row_lock', 'atomic_update', 'redis_counter', 'unit_rows')
);

-- The sweeper looks for active holds that are past their expiry.
CREATE INDEX holds_active_expiry_idx ON holds (expires_at) WHERE status = 'active';
