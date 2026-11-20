-- One live hold per buyer per drop, enforced by the database itself.
-- "Partial" index: it only covers rows that are still live, so a buyer whose hold
-- expired or was released can try again.
CREATE UNIQUE INDEX holds_one_live_per_user_idx
    ON holds (drop_id, user_id)
    WHERE status IN ('active', 'confirmed');

-- Stock can never be negative. Note what this does NOT catch: the naive strategy
-- writes back a number it computed from a stale read, and that number is never negative.
ALTER TABLE inventory_counters
    ADD CONSTRAINT inventory_counters_available_not_negative CHECK (available >= 0);

ALTER TABLE drops
    ADD COLUMN price_cents integer NOT NULL DEFAULT 1000 CHECK (price_cents >= 0);

-- An order is a hold that was checked out. UNIQUE (hold_id) means one hold can
-- never turn into two orders, even if checkout is called twice at the same time.
CREATE TABLE orders (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hold_id      uuid NOT NULL UNIQUE REFERENCES holds (id) ON DELETE CASCADE,
    drop_id      uuid NOT NULL REFERENCES drops (id) ON DELETE CASCADE,
    user_id      uuid NOT NULL,
    status       text NOT NULL CHECK (status IN ('pending', 'paid', 'failed')),
    amount_cents integer NOT NULL CHECK (amount_cents >= 0),
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX orders_drop_idx ON orders (drop_id);
