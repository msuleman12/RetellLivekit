-- Step 1 of 2: permissions.
--
-- Before running: create the database "bblg_livekit_intake" in pgAdmin
-- (right-click Databases > Create > Database...).
--
-- Run in a Query Tool connected to the "bblg_livekit_intake" database as doadmin.
-- Lets the agent's login (agent_intake_staging) connect and create its tables,
-- and lets doadmin create them on its behalf in step 2.

GRANT CONNECT ON DATABASE bblg_livekit_intake TO agent_intake_staging;
GRANT USAGE, CREATE ON SCHEMA public TO agent_intake_staging;
GRANT agent_intake_staging TO doadmin;
