CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS cards (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    balance NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
    active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS rides (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    price NUMERIC(12,2) NOT NULL CHECK (price > 0),
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    current_occupancy INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'out_of_service', 'deleted')),
    CHECK (current_occupancy >= 0 AND current_occupancy <= capacity)
);
CREATE TABLE IF NOT EXISTS movements (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    card_id BIGINT NOT NULL REFERENCES cards(id),
    ride_id BIGINT REFERENCES rides(id),
    type TEXT NOT NULL CHECK (type IN ('recharge', 'ride_access')),
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS movements_card_idx ON movements(card_id, id);
CREATE TABLE IF NOT EXISTS operations (
    actor TEXT NOT NULL,
    operation_id UUID NOT NULL,
    fingerprint TEXT NOT NULL,
    response JSONB NOT NULL,
    PRIMARY KEY (actor, operation_id)
);
