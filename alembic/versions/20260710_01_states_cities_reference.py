"""Add states/cities reference tables, seeded with a launch-scope dataset

Revision ID: 20260710_01
Revises: 20260601_01
Create Date: 2026-07-10

Why this migration exists:
  - PRD requires server-side State/City validation on traveler/agency signup.
    No location reference data existed anywhere in the schema before this —
    state/city were free-text strings with no validation.

Scope note: the seed data below is the 28 states + 8 union territories (an
authoritative, complete list) each with a curated set of major/tourist-hub
cities relevant to a travel platform — NOT an exhaustive census-level city
list. State validation is meant to be strict (the state list is complete).
City validation should stay lenient against this table (see
app/services/locations.py) since many real, valid signups will name a town
not in this seed set — hard-rejecting those would block real users.

Tables and column names use the camelCase identifiers this repo's other
Prisma-derived tables use, for consistency.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260710_01"
down_revision = "20260601_01"
branch_labels = None
depends_on = None


# name -> (code, [cities...])
STATES = {
    "Andhra Pradesh": ("AP", ["Visakhapatnam", "Vijayawada", "Tirupati", "Guntur", "Nellore", "Kakinada", "Amaravati"]),
    "Arunachal Pradesh": ("AR", ["Itanagar", "Tawang", "Ziro", "Bomdila"]),
    "Assam": ("AS", ["Guwahati", "Dibrugarh", "Jorhat", "Silchar", "Tezpur", "Kaziranga"]),
    "Bihar": ("BR", ["Patna", "Gaya", "Bodh Gaya", "Nalanda", "Rajgir", "Muzaffarpur", "Bhagalpur"]),
    "Chhattisgarh": ("CG", ["Raipur", "Bilaspur", "Jagdalpur", "Bastar"]),
    "Goa": ("GA", ["Panaji", "Margao", "Vasco da Gama", "Calangute", "Anjuna"]),
    "Gujarat": ("GJ", ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Gandhinagar", "Dwarka", "Somnath", "Gir"]),
    "Haryana": ("HR", ["Gurugram", "Faridabad", "Panipat", "Kurukshetra", "Panchkula"]),
    "Himachal Pradesh": ("HP", ["Shimla", "Manali", "Dharamshala", "Kasol", "Dalhousie", "Kullu", "Spiti"]),
    "Jharkhand": ("JH", ["Ranchi", "Jamshedpur", "Dhanbad", "Netarhat"]),
    "Karnataka": ("KA", ["Bengaluru", "Mysuru", "Coorg", "Hampi", "Mangaluru", "Chikmagalur", "Gokarna", "Udupi"]),
    "Kerala": ("KL", ["Kochi", "Thiruvananthapuram", "Munnar", "Alleppey", "Wayanad", "Kovalam", "Kumarakom", "Kozhikode"]),
    "Madhya Pradesh": ("MP", ["Bhopal", "Indore", "Gwalior", "Khajuraho", "Ujjain", "Pachmarhi", "Orchha"]),
    "Maharashtra": ("MH", ["Mumbai", "Pune", "Nagpur", "Nashik", "Lonavala", "Mahabaleshwar", "Aurangabad", "Alibaug"]),
    "Manipur": ("MN", ["Imphal", "Loktak"]),
    "Meghalaya": ("ML", ["Shillong", "Cherrapunji", "Dawki"]),
    "Mizoram": ("MZ", ["Aizawl"]),
    "Nagaland": ("NL", ["Kohima", "Dimapur"]),
    "Odisha": ("OD", ["Bhubaneswar", "Puri", "Konark", "Cuttack"]),
    "Punjab": ("PB", ["Amritsar", "Chandigarh", "Ludhiana", "Jalandhar", "Patiala"]),
    "Rajasthan": ("RJ", ["Jaipur", "Udaipur", "Jodhpur", "Jaisalmer", "Pushkar", "Mount Abu", "Bikaner", "Ajmer"]),
    "Sikkim": ("SK", ["Gangtok", "Pelling", "Lachung", "Yuksom"]),
    "Tamil Nadu": ("TN", ["Chennai", "Coimbatore", "Madurai", "Ooty", "Kodaikanal", "Rameswaram", "Mahabalipuram", "Kanyakumari"]),
    "Telangana": ("TG", ["Hyderabad", "Warangal", "Nizamabad"]),
    "Tripura": ("TR", ["Agartala"]),
    "Uttar Pradesh": ("UP", ["Lucknow", "Agra", "Varanasi", "Noida", "Kanpur", "Prayagraj", "Mathura", "Ayodhya"]),
    "Uttarakhand": ("UK", ["Dehradun", "Rishikesh", "Haridwar", "Nainital", "Mussoorie", "Auli", "Jim Corbett"]),
    "West Bengal": ("WB", ["Kolkata", "Darjeeling", "Siliguri", "Digha", "Kalimpong", "Sundarbans"]),
    # Union territories
    "Andaman and Nicobar Islands": ("AN", ["Port Blair", "Havelock Island"]),
    "Chandigarh": ("CH", ["Chandigarh"]),
    "Dadra and Nagar Haveli and Daman and Diu": ("DN", ["Daman", "Diu", "Silvassa"]),
    "Delhi": ("DL", ["New Delhi", "Delhi"]),
    "Jammu and Kashmir": ("JK", ["Srinagar", "Jammu", "Gulmarg", "Pahalgam", "Leh"]),
    "Ladakh": ("LA", ["Leh", "Kargil", "Nubra Valley"]),
    "Lakshadweep": ("LD", ["Kavaratti"]),
    "Puducherry": ("PY", ["Puducherry", "Auroville"]),
}


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS states (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            code VARCHAR(10) NOT NULL UNIQUE,
            "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cities (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            "stateId" VARCHAR(36) NOT NULL REFERENCES states(id),
            "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS ix_cities_state_id ON cities ("stateId")')
    op.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_cities_name_state ON cities (name, "stateId")')

    states_table = sa.table(
        "states",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("code", sa.String),
    )
    cities_table = sa.table(
        "cities",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("stateId", sa.String),
    )

    state_rows = []
    city_rows = []
    for name, (code, cities) in STATES.items():
        state_id = str(uuid.uuid4())
        state_rows.append({"id": state_id, "name": name, "code": code})
        for city_name in cities:
            city_rows.append({"id": str(uuid.uuid4()), "name": city_name, "stateId": state_id})

    op.bulk_insert(states_table, state_rows)
    op.bulk_insert(cities_table, city_rows)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cities")
    op.execute("DROP TABLE IF EXISTS states")
