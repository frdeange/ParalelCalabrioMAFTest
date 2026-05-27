SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRAN;

    DECLARE @Now DATETIME2(0) = CAST('2026-05-20T18:13:00' AS DATETIME2(0));
    DECLARE @Today DATE = CAST(@Now AS DATE);
    DECLARE @BuId INT;

    IF NOT EXISTS (SELECT 1 FROM wfm.business_unit WHERE code = N'CWFM-DEMO')
    BEGIN
        INSERT INTO wfm.business_unit (name, code)
        VALUES (N'Calabrio WFM Demo', N'CWFM-DEMO');
    END;

    SELECT @BuId = bu_id
    FROM wfm.business_unit
    WHERE code = N'CWFM-DEMO';

    IF NOT EXISTS (SELECT 1 FROM wfm.site WHERE bu_id = @BuId AND name = N'Stockholm')
        INSERT INTO wfm.site (bu_id, name, timezone) VALUES (@BuId, N'Stockholm', N'Europe/Stockholm');
    IF NOT EXISTS (SELECT 1 FROM wfm.site WHERE bu_id = @BuId AND name = N'London')
        INSERT INTO wfm.site (bu_id, name, timezone) VALUES (@BuId, N'London', N'Europe/London');
    IF NOT EXISTS (SELECT 1 FROM wfm.site WHERE bu_id = @BuId AND name = N'Madrid')
        INSERT INTO wfm.site (bu_id, name, timezone) VALUES (@BuId, N'Madrid', N'Europe/Madrid');

    IF NOT EXISTS (SELECT 1 FROM wfm.team WHERE bu_id = @BuId AND name = N'Stockholm Support')
        INSERT INTO wfm.team (bu_id, name, manager_agent_id) VALUES (@BuId, N'Stockholm Support', NULL);
    IF NOT EXISTS (SELECT 1 FROM wfm.team WHERE bu_id = @BuId AND name = N'Stockholm Sales')
        INSERT INTO wfm.team (bu_id, name, manager_agent_id) VALUES (@BuId, N'Stockholm Sales', NULL);
    IF NOT EXISTS (SELECT 1 FROM wfm.team WHERE bu_id = @BuId AND name = N'London Support')
        INSERT INTO wfm.team (bu_id, name, manager_agent_id) VALUES (@BuId, N'London Support', NULL);
    IF NOT EXISTS (SELECT 1 FROM wfm.team WHERE bu_id = @BuId AND name = N'London Sales')
        INSERT INTO wfm.team (bu_id, name, manager_agent_id) VALUES (@BuId, N'London Sales', NULL);
    IF NOT EXISTS (SELECT 1 FROM wfm.team WHERE bu_id = @BuId AND name = N'Madrid Support')
        INSERT INTO wfm.team (bu_id, name, manager_agent_id) VALUES (@BuId, N'Madrid Support', NULL);
    IF NOT EXISTS (SELECT 1 FROM wfm.team WHERE bu_id = @BuId AND name = N'Madrid Sales')
        INSERT INTO wfm.team (bu_id, name, manager_agent_id) VALUES (@BuId, N'Madrid Sales', NULL);

    INSERT INTO wfm.skill (bu_id, name)
    SELECT @BuId, src.name
    FROM (VALUES
        (N'Voice Inbound'),
        (N'Voice Outbound'),
        (N'Chat'),
        (N'Email'),
        (N'Back Office'),
        (N'Social Media'),
        (N'Training'),
        (N'Escalation')
    ) AS src(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM wfm.skill AS sk
        WHERE sk.bu_id = @BuId
          AND sk.name = src.name
    );

    ;WITH agent_seed AS (
        SELECT
            ROW_NUMBER() OVER (ORDER BY src.employee_id) AS rn,
            src.full_name,
            src.employee_id,
            src.team_name,
            src.site_name
        FROM (VALUES
            (N'Anna Lindholm', N'CWFM-0001', N'Stockholm Support', N'Stockholm'),
            (N'Johan Nyberg', N'CWFM-0002', N'Stockholm Support', N'Stockholm'),
            (N'Elin Bergqvist', N'CWFM-0003', N'Stockholm Support', N'Stockholm'),
            (N'Markus Olsson', N'CWFM-0004', N'Stockholm Support', N'Stockholm'),
            (N'Sofia Sundberg', N'CWFM-0005', N'Stockholm Support', N'Stockholm'),
            (N'Erik Holmgren', N'CWFM-0006', N'Stockholm Support', N'Stockholm'),
            (N'Maja Karlsson', N'CWFM-0007', N'Stockholm Support', N'Stockholm'),
            (N'Viktor Rosen', N'CWFM-0008', N'Stockholm Support', N'Stockholm'),
            (N'Lina Pettersson', N'CWFM-0009', N'Stockholm Support', N'Stockholm'),
            (N'Oskar Sjostrom', N'CWFM-0010', N'Stockholm Sales', N'Stockholm'),
            (N'Emma Wallin', N'CWFM-0011', N'Stockholm Sales', N'Stockholm'),
            (N'Daniel Ahlberg', N'CWFM-0012', N'Stockholm Sales', N'Stockholm'),
            (N'Sara Norberg', N'CWFM-0013', N'Stockholm Sales', N'Stockholm'),
            (N'Niklas Engstrom', N'CWFM-0014', N'Stockholm Sales', N'Stockholm'),
            (N'Frida Dahl', N'CWFM-0015', N'Stockholm Sales', N'Stockholm'),
            (N'Gustav Lind', N'CWFM-0016', N'Stockholm Sales', N'Stockholm'),
            (N'Ida Falk', N'CWFM-0017', N'Stockholm Sales', N'Stockholm'),
            (N'Henrik Broman', N'CWFM-0018', N'London Support', N'London'),
            (N'Olivia Carter', N'CWFM-0019', N'London Support', N'London'),
            (N'Ethan Davies', N'CWFM-0020', N'London Support', N'London'),
            (N'Grace Thompson', N'CWFM-0021', N'London Support', N'London'),
            (N'Harry Wilson', N'CWFM-0022', N'London Support', N'London'),
            (N'Ruby Mitchell', N'CWFM-0023', N'London Support', N'London'),
            (N'Noah Edwards', N'CWFM-0024', N'London Support', N'London'),
            (N'Freya Hughes', N'CWFM-0025', N'London Support', N'London'),
            (N'Jack Bennett', N'CWFM-0026', N'London Support', N'London'),
            (N'Emily Clark', N'CWFM-0027', N'London Sales', N'London'),
            (N'Leo Robinson', N'CWFM-0028', N'London Sales', N'London'),
            (N'Chloe Martin', N'CWFM-0029', N'London Sales', N'London'),
            (N'George Lewis', N'CWFM-0030', N'London Sales', N'London'),
            (N'Isla Hall', N'CWFM-0031', N'London Sales', N'London'),
            (N'Arthur King', N'CWFM-0032', N'London Sales', N'London'),
            (N'Poppy Scott', N'CWFM-0033', N'London Sales', N'London'),
            (N'Mason Green', N'CWFM-0034', N'London Sales', N'London'),
            (N'Amelia Adams', N'CWFM-0035', N'Madrid Support', N'Madrid'),
            (N'Carlos Ortega', N'CWFM-0036', N'Madrid Support', N'Madrid'),
            (N'Lucia Serrano', N'CWFM-0037', N'Madrid Support', N'Madrid'),
            (N'Diego Navarro', N'CWFM-0038', N'Madrid Support', N'Madrid'),
            (N'Elena Molina', N'CWFM-0039', N'Madrid Support', N'Madrid'),
            (N'Pablo Ruiz', N'CWFM-0040', N'Madrid Support', N'Madrid'),
            (N'Nuria Campos', N'CWFM-0041', N'Madrid Support', N'Madrid'),
            (N'Javier Flores', N'CWFM-0042', N'Madrid Support', N'Madrid'),
            (N'Clara Vega', N'CWFM-0043', N'Madrid Sales', N'Madrid'),
            (N'Miguel Prieto', N'CWFM-0044', N'Madrid Sales', N'Madrid'),
            (N'Laura Gil', N'CWFM-0045', N'Madrid Sales', N'Madrid'),
            (N'Alvaro Sanz', N'CWFM-0046', N'Madrid Sales', N'Madrid'),
            (N'Paula Cano', N'CWFM-0047', N'Madrid Sales', N'Madrid'),
            (N'Adrian Bravo', N'CWFM-0048', N'Madrid Sales', N'Madrid'),
            (N'Marta Lozano', N'CWFM-0049', N'Madrid Sales', N'Madrid'),
            (N'Sergio Mendez', N'CWFM-0050', N'Madrid Sales', N'Madrid')
        ) AS src(full_name, employee_id, team_name, site_name)
    )
    INSERT INTO wfm.agent (
        bu_id, full_name, employee_id, team_id, site_id, hire_date, termination_date, status
    )
    SELECT
        @BuId,
        a.full_name,
        a.employee_id,
        t.team_id,
        s.site_id,
        DATEADD(DAY, -1 * ((a.rn * 37) % 1400), @Today),
        NULL,
        N'Active'
    FROM agent_seed AS a
    INNER JOIN wfm.team AS t
        ON t.bu_id = @BuId
       AND t.name = a.team_name
    INNER JOIN wfm.site AS s
        ON s.bu_id = @BuId
       AND s.name = a.site_name
    WHERE NOT EXISTS (
        SELECT 1
        FROM wfm.agent AS ag
        WHERE ag.bu_id = @BuId
          AND ag.employee_id = a.employee_id
    );

    ;WITH team_manager AS (
        SELECT
            a.team_id,
            MIN(a.agent_id) AS manager_agent_id
        FROM wfm.agent AS a
        WHERE a.bu_id = @BuId
        GROUP BY a.team_id
    )
    UPDATE t
    SET t.manager_agent_id = tm.manager_agent_id
    FROM wfm.team AS t
    INNER JOIN team_manager AS tm
        ON t.team_id = tm.team_id
    WHERE t.bu_id = @BuId
      AND (t.manager_agent_id IS NULL OR t.manager_agent_id <> tm.manager_agent_id);

    INSERT INTO wfm.agent_skill (agent_id, skill_id, proficiency)
    SELECT
        a.agent_id,
        x.skill_id,
        CASE ABS(CHECKSUM(a.agent_id, x.skill_id, N'proficiency')) % 3
            WHEN 0 THEN N'Beginner'
            WHEN 1 THEN N'Intermediate'
            ELSE N'Expert'
        END AS proficiency
    FROM wfm.agent AS a
    CROSS APPLY (
        SELECT TOP (2 + ABS(CHECKSUM(a.agent_id)) % 3)
            s.skill_id
        FROM wfm.skill AS s
        WHERE s.bu_id = @BuId
        ORDER BY ABS(CHECKSUM(a.agent_id, s.skill_id))
    ) AS x
    WHERE a.bu_id = @BuId
      AND NOT EXISTS (
          SELECT 1
          FROM wfm.agent_skill AS askill
          WHERE askill.agent_id = a.agent_id
            AND askill.skill_id = x.skill_id
      );

    INSERT INTO absence.absence_type (bu_id, name, paid)
    SELECT @BuId, src.name, src.paid
    FROM (VALUES
        (N'Vacation', 1),
        (N'Sick Leave', 1),
        (N'Personal Day', 1),
        (N'Parental Leave', 1),
        (N'Training', 1),
        (N'Jury Duty', 0)
    ) AS src(name, paid)
    WHERE NOT EXISTS (
        SELECT 1
        FROM absence.absence_type AS atp
        WHERE atp.bu_id = @BuId
          AND atp.name = src.name
    );

    IF NOT EXISTS (SELECT 1 FROM absence.absence_request WHERE bu_id = @BuId)
    BEGIN
        ;WITH n AS (
            SELECT TOP (1000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n
            FROM sys.all_objects a
            CROSS JOIN sys.all_objects b
        ),
        agents AS (
            SELECT
                a.agent_id,
                ROW_NUMBER() OVER (ORDER BY a.agent_id) AS rn
            FROM wfm.agent AS a
            WHERE a.bu_id = @BuId
        ),
        abs_types AS (
            SELECT
                atp.absence_type_id,
                ROW_NUMBER() OVER (ORDER BY atp.absence_type_id) AS rn
            FROM absence.absence_type AS atp
            WHERE atp.bu_id = @BuId
        ),
        counts AS (
            SELECT
                (SELECT COUNT(1) FROM agents) AS agent_count,
                (SELECT COUNT(1) FROM abs_types) AS type_count
        )
        INSERT INTO absence.absence_request (
            bu_id, agent_id, absence_type_id, start_date, end_date, status, requested_at, decided_by, decided_at
        )
        SELECT
            @BuId,
            a.agent_id,
            t.absence_type_id,
            src.start_date,
            DATEADD(DAY, src.duration_days - 1, src.start_date) AS end_date,
            src.status,
            req.requested_at,
            req.decided_by,
            req.decided_at
        FROM n
        CROSS JOIN counts AS c
        INNER JOIN agents AS a
            ON a.rn = ((n.n - 1) % c.agent_count) + 1
        INNER JOIN abs_types AS t
            ON t.rn = ((n.n - 1) % c.type_count) + 1
        CROSS APPLY (
            SELECT
                DATEADD(DAY, -1 * ((n.n * 7) % 180), @Today) AS start_date,
                1 + (n.n % 5) AS duration_days,
                CASE
                    WHEN (n.n % 100) < 70 THEN N'Approved'
                    WHEN (n.n % 100) < 85 THEN N'Denied'
                    ELSE N'Pending'
                END AS status
        ) AS src
        CROSS APPLY (
            SELECT
                DATEADD(HOUR, n.n % 12, DATEADD(DAY, -1 * (3 + (n.n % 21)), CAST(src.start_date AS DATETIME2(0)))) AS requested_at,
                CASE
                    WHEN src.status = N'Pending' THEN NULL
                    ELSE N'WFM Supervisor'
                END AS decided_by,
                CASE
                    WHEN src.status = N'Pending' THEN NULL
                    ELSE DATEADD(HOUR, 4 + (n.n % 48), DATEADD(HOUR, n.n % 12, DATEADD(DAY, -1 * (3 + (n.n % 21)), CAST(src.start_date AS DATETIME2(0)))))
                END AS decided_at
        ) AS req;
    END;

    IF NOT EXISTS (SELECT 1 FROM overtime.overtime_request WHERE bu_id = @BuId)
    BEGIN
        ;WITH n AS (
            SELECT TOP (200) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n
            FROM sys.all_objects
        ),
        agents AS (
            SELECT
                a.agent_id,
                ROW_NUMBER() OVER (ORDER BY a.agent_id) AS rn
            FROM wfm.agent AS a
            WHERE a.bu_id = @BuId
        ),
        counts AS (
            SELECT (SELECT COUNT(1) FROM agents) AS agent_count
        )
        INSERT INTO overtime.overtime_request (
            bu_id, agent_id, [date], hours, status, reason, requested_at, approved_by, approved_at
        )
        SELECT
            @BuId,
            a.agent_id,
            src.ot_date,
            src.hours,
            src.status,
            src.reason,
            src.requested_at,
            CASE
                WHEN src.status = N'Pending' THEN NULL
                ELSE N'WFM Supervisor'
            END AS approved_by,
            CASE
                WHEN src.status = N'Pending' THEN NULL
                ELSE DATEADD(HOUR, 8, src.requested_at)
            END AS approved_at
        FROM n
        CROSS JOIN counts AS c
        INNER JOIN agents AS a
            ON a.rn = ((n.n - 1) % c.agent_count) + 1
        CROSS APPLY (
            SELECT
                DATEADD(DAY, -1 * (n.n % 90), @Today) AS ot_date,
                CAST(1.00 + ((n.n % 7) * 0.50) AS DECIMAL(4,2)) AS hours,
                CASE
                    WHEN (n.n % 10) < 5 THEN N'Approved'
                    WHEN (n.n % 10) < 8 THEN N'Pending'
                    ELSE N'Denied'
                END AS status,
                CASE n.n % 6
                    WHEN 0 THEN N'Unexpected call volume increase'
                    WHEN 1 THEN N'Backlog clearance before weekend'
                    WHEN 2 THEN N'Complex escalations requiring follow-up'
                    WHEN 3 THEN N'Coverage for sick colleague'
                    WHEN 4 THEN N'Training and shadowing overtime'
                    ELSE N'Late campaign support'
                END AS reason,
                DATEADD(HOUR, n.n % 10, DATEADD(DAY, -1 * ((n.n % 90) + 2), CAST(@Today AS DATETIME2(0)))) AS requested_at
        ) AS src;
    END;

    IF NOT EXISTS (SELECT 1 FROM scheduling.shift WHERE bu_id = @BuId)
    BEGIN
        ;WITH n AS (
            SELECT TOP (500) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n
            FROM sys.all_objects a
            CROSS JOIN sys.all_objects b
        ),
        agents AS (
            SELECT
                a.agent_id,
                ROW_NUMBER() OVER (ORDER BY a.agent_id) AS rn
            FROM wfm.agent AS a
            WHERE a.bu_id = @BuId
        ),
        counts AS (
            SELECT (SELECT COUNT(1) FROM agents) AS agent_count
        )
        INSERT INTO scheduling.shift (
            bu_id, agent_id, [date], shift_name, start_time, end_time, queue_name
        )
        SELECT
            @BuId,
            a.agent_id,
            DATEADD(DAY, (n.n - 1) % 14, @Today) AS [date],
            CASE n.n % 3
                WHEN 0 THEN N'Morning'
                WHEN 1 THEN N'Afternoon'
                ELSE N'Night'
            END AS shift_name,
            CASE n.n % 3
                WHEN 0 THEN CAST(N'06:00:00' AS TIME(0))
                WHEN 1 THEN CAST(N'14:00:00' AS TIME(0))
                ELSE CAST(N'22:00:00' AS TIME(0))
            END AS start_time,
            CASE n.n % 3
                WHEN 0 THEN CAST(N'14:00:00' AS TIME(0))
                WHEN 1 THEN CAST(N'22:00:00' AS TIME(0))
                ELSE CAST(N'06:00:00' AS TIME(0))
            END AS end_time,
            CASE n.n % 7
                WHEN 0 THEN N'General Support'
                WHEN 1 THEN N'Sales Inbound'
                WHEN 2 THEN N'Sales Outbound'
                WHEN 3 THEN N'Tech Escalations'
                WHEN 4 THEN N'Billing'
                WHEN 5 THEN N'Chat Operations'
                ELSE N'Social Care'
            END AS queue_name
        FROM n
        CROSS JOIN counts AS c
        INNER JOIN agents AS a
            ON a.rn = ((n.n - 1) % c.agent_count) + 1;
    END;

    UPDATE target
    SET
        schema_name = src.schema_name,
        description = src.description,
        keywords = src.keywords,
        is_active = src.is_active
    FROM _metadata.catalog_tables AS target
    INNER JOIN (VALUES
        (N'analytics.vw_PersonDetail', N'analytics', N'Employee and agent profiles with team and site information', N'agent,employee,person,team,site,hire,staff,headcount,roster', CAST(1 AS BIT)),
        (N'analytics.vw_AbsenceRequest', N'analytics', N'Absence and leave requests including status and duration', N'absence,leave,pto,dayoff,vacation,sick,request,time off', CAST(1 AS BIT)),
        (N'analytics.vw_OvertimeRequest', N'analytics', N'Overtime requests, extra hours and approval lifecycle', N'overtime,ot,extra hours,extended shift,approval,workload', CAST(1 AS BIT)),
        (N'analytics.vw_Scheduling', N'analytics', N'Planned shifts by agent and team for workforce scheduling', N'schedule,shift,roster,planning,coverage,queue,staffing', CAST(1 AS BIT))
    ) AS src(table_name, schema_name, description, keywords, is_active)
        ON target.table_name = src.table_name;

    INSERT INTO _metadata.catalog_tables (table_name, schema_name, description, keywords, is_active)
    SELECT src.table_name, src.schema_name, src.description, src.keywords, src.is_active
    FROM (VALUES
        (N'analytics.vw_PersonDetail', N'analytics', N'Employee and agent profiles with team and site information', N'agent,employee,person,team,site,hire,staff,headcount,roster', CAST(1 AS BIT)),
        (N'analytics.vw_AbsenceRequest', N'analytics', N'Absence and leave requests including status and duration', N'absence,leave,pto,dayoff,vacation,sick,request,time off', CAST(1 AS BIT)),
        (N'analytics.vw_OvertimeRequest', N'analytics', N'Overtime requests, extra hours and approval lifecycle', N'overtime,ot,extra hours,extended shift,approval,workload', CAST(1 AS BIT)),
        (N'analytics.vw_Scheduling', N'analytics', N'Planned shifts by agent and team for workforce scheduling', N'schedule,shift,roster,planning,coverage,queue,staffing', CAST(1 AS BIT))
    ) AS src(table_name, schema_name, description, keywords, is_active)
    WHERE NOT EXISTS (
        SELECT 1
        FROM _metadata.catalog_tables AS target
        WHERE target.table_name = src.table_name
    );

    UPDATE target
    SET
        data_type = src.data_type,
        is_nullable = src.is_nullable,
        description = src.description,
        display_name = src.display_name
    FROM _metadata.catalog_columns AS target
    INNER JOIN (VALUES
        (N'analytics.vw_PersonDetail', N'agent_id', N'INT', CAST(0 AS BIT), N'Unique identifier of the agent', N'Agent ID'),
        (N'analytics.vw_PersonDetail', N'full_name', N'NVARCHAR(150)', CAST(0 AS BIT), N'Full legal name of the employee', N'Agent Name'),
        (N'analytics.vw_PersonDetail', N'employee_id', N'NVARCHAR(20)', CAST(0 AS BIT), N'Internal employee code', N'Employee ID'),
        (N'analytics.vw_PersonDetail', N'team_name', N'NVARCHAR(100)', CAST(0 AS BIT), N'Assigned operational team', N'Team'),
        (N'analytics.vw_PersonDetail', N'site_name', N'NVARCHAR(100)', CAST(0 AS BIT), N'Physical site or hub', N'Site'),
        (N'analytics.vw_PersonDetail', N'hire_date', N'DATE', CAST(0 AS BIT), N'Date the agent joined the company', N'Hire Date'),
        (N'analytics.vw_PersonDetail', N'status', N'NVARCHAR(20)', CAST(0 AS BIT), N'Current employment status', N'Status'),
        (N'analytics.vw_PersonDetail', N'bu_id', N'INT', CAST(0 AS BIT), N'Business unit identifier for tenant scoping', N'Business Unit ID'),

        (N'analytics.vw_AbsenceRequest', N'request_id', N'INT', CAST(0 AS BIT), N'Unique absence request identifier', N'Request ID'),
        (N'analytics.vw_AbsenceRequest', N'agent_id', N'INT', CAST(0 AS BIT), N'Identifier of the requested agent', N'Agent ID'),
        (N'analytics.vw_AbsenceRequest', N'agent_name', N'NVARCHAR(150)', CAST(0 AS BIT), N'Agent full name', N'Agent Name'),
        (N'analytics.vw_AbsenceRequest', N'absence_type_name', N'NVARCHAR(50)', CAST(0 AS BIT), N'Type of leave requested', N'Absence Type'),
        (N'analytics.vw_AbsenceRequest', N'start_date', N'DATE', CAST(0 AS BIT), N'First date of absence', N'Start Date'),
        (N'analytics.vw_AbsenceRequest', N'end_date', N'DATE', CAST(0 AS BIT), N'Last date of absence', N'End Date'),
        (N'analytics.vw_AbsenceRequest', N'status', N'NVARCHAR(20)', CAST(0 AS BIT), N'Approval workflow status', N'Status'),
        (N'analytics.vw_AbsenceRequest', N'days', N'INT', CAST(0 AS BIT), N'Calendar days covered by the request', N'Days'),
        (N'analytics.vw_AbsenceRequest', N'bu_id', N'INT', CAST(0 AS BIT), N'Business unit identifier for tenant scoping', N'Business Unit ID'),

        (N'analytics.vw_OvertimeRequest', N'ot_id', N'INT', CAST(0 AS BIT), N'Unique overtime request identifier', N'Overtime ID'),
        (N'analytics.vw_OvertimeRequest', N'agent_id', N'INT', CAST(0 AS BIT), N'Identifier of the requested agent', N'Agent ID'),
        (N'analytics.vw_OvertimeRequest', N'agent_name', N'NVARCHAR(150)', CAST(0 AS BIT), N'Agent full name', N'Agent Name'),
        (N'analytics.vw_OvertimeRequest', N'date', N'DATE', CAST(0 AS BIT), N'Date the overtime was requested for', N'Date'),
        (N'analytics.vw_OvertimeRequest', N'hours', N'DECIMAL(4,2)', CAST(0 AS BIT), N'Amount of overtime hours requested', N'Hours'),
        (N'analytics.vw_OvertimeRequest', N'status', N'NVARCHAR(20)', CAST(0 AS BIT), N'Approval workflow status', N'Status'),
        (N'analytics.vw_OvertimeRequest', N'reason', N'NVARCHAR(500)', CAST(0 AS BIT), N'Business reason for extra hours', N'Reason'),
        (N'analytics.vw_OvertimeRequest', N'bu_id', N'INT', CAST(0 AS BIT), N'Business unit identifier for tenant scoping', N'Business Unit ID'),

        (N'analytics.vw_Scheduling', N'shift_id', N'INT', CAST(0 AS BIT), N'Unique shift record identifier', N'Shift ID'),
        (N'analytics.vw_Scheduling', N'agent_id', N'INT', CAST(0 AS BIT), N'Identifier of the assigned agent', N'Agent ID'),
        (N'analytics.vw_Scheduling', N'agent_name', N'NVARCHAR(150)', CAST(0 AS BIT), N'Agent full name', N'Agent Name'),
        (N'analytics.vw_Scheduling', N'team_name', N'NVARCHAR(100)', CAST(0 AS BIT), N'Operational team handling the shift', N'Team'),
        (N'analytics.vw_Scheduling', N'date', N'DATE', CAST(0 AS BIT), N'Date of the scheduled shift', N'Date'),
        (N'analytics.vw_Scheduling', N'shift_name', N'NVARCHAR(50)', CAST(0 AS BIT), N'Shift category name', N'Shift'),
        (N'analytics.vw_Scheduling', N'start_time', N'TIME', CAST(0 AS BIT), N'Shift start time', N'Start Time'),
        (N'analytics.vw_Scheduling', N'end_time', N'TIME', CAST(0 AS BIT), N'Shift end time', N'End Time'),
        (N'analytics.vw_Scheduling', N'queue_name', N'NVARCHAR(100)', CAST(1 AS BIT), N'Queue assigned for the shift', N'Queue'),
        (N'analytics.vw_Scheduling', N'bu_id', N'INT', CAST(0 AS BIT), N'Business unit identifier for tenant scoping', N'Business Unit ID')
    ) AS src(table_name, column_name, data_type, is_nullable, description, display_name)
        ON target.table_name = src.table_name
       AND target.column_name = src.column_name;

    INSERT INTO _metadata.catalog_columns (table_name, column_name, data_type, is_nullable, description, display_name)
    SELECT src.table_name, src.column_name, src.data_type, src.is_nullable, src.description, src.display_name
    FROM (VALUES
        (N'analytics.vw_PersonDetail', N'agent_id', N'INT', CAST(0 AS BIT), N'Unique identifier of the agent', N'Agent ID'),
        (N'analytics.vw_PersonDetail', N'full_name', N'NVARCHAR(150)', CAST(0 AS BIT), N'Full legal name of the employee', N'Agent Name'),
        (N'analytics.vw_PersonDetail', N'employee_id', N'NVARCHAR(20)', CAST(0 AS BIT), N'Internal employee code', N'Employee ID'),
        (N'analytics.vw_PersonDetail', N'team_name', N'NVARCHAR(100)', CAST(0 AS BIT), N'Assigned operational team', N'Team'),
        (N'analytics.vw_PersonDetail', N'site_name', N'NVARCHAR(100)', CAST(0 AS BIT), N'Physical site or hub', N'Site'),
        (N'analytics.vw_PersonDetail', N'hire_date', N'DATE', CAST(0 AS BIT), N'Date the agent joined the company', N'Hire Date'),
        (N'analytics.vw_PersonDetail', N'status', N'NVARCHAR(20)', CAST(0 AS BIT), N'Current employment status', N'Status'),
        (N'analytics.vw_PersonDetail', N'bu_id', N'INT', CAST(0 AS BIT), N'Business unit identifier for tenant scoping', N'Business Unit ID'),

        (N'analytics.vw_AbsenceRequest', N'request_id', N'INT', CAST(0 AS BIT), N'Unique absence request identifier', N'Request ID'),
        (N'analytics.vw_AbsenceRequest', N'agent_id', N'INT', CAST(0 AS BIT), N'Identifier of the requested agent', N'Agent ID'),
        (N'analytics.vw_AbsenceRequest', N'agent_name', N'NVARCHAR(150)', CAST(0 AS BIT), N'Agent full name', N'Agent Name'),
        (N'analytics.vw_AbsenceRequest', N'absence_type_name', N'NVARCHAR(50)', CAST(0 AS BIT), N'Type of leave requested', N'Absence Type'),
        (N'analytics.vw_AbsenceRequest', N'start_date', N'DATE', CAST(0 AS BIT), N'First date of absence', N'Start Date'),
        (N'analytics.vw_AbsenceRequest', N'end_date', N'DATE', CAST(0 AS BIT), N'Last date of absence', N'End Date'),
        (N'analytics.vw_AbsenceRequest', N'status', N'NVARCHAR(20)', CAST(0 AS BIT), N'Approval workflow status', N'Status'),
        (N'analytics.vw_AbsenceRequest', N'days', N'INT', CAST(0 AS BIT), N'Calendar days covered by the request', N'Days'),
        (N'analytics.vw_AbsenceRequest', N'bu_id', N'INT', CAST(0 AS BIT), N'Business unit identifier for tenant scoping', N'Business Unit ID'),

        (N'analytics.vw_OvertimeRequest', N'ot_id', N'INT', CAST(0 AS BIT), N'Unique overtime request identifier', N'Overtime ID'),
        (N'analytics.vw_OvertimeRequest', N'agent_id', N'INT', CAST(0 AS BIT), N'Identifier of the requested agent', N'Agent ID'),
        (N'analytics.vw_OvertimeRequest', N'agent_name', N'NVARCHAR(150)', CAST(0 AS BIT), N'Agent full name', N'Agent Name'),
        (N'analytics.vw_OvertimeRequest', N'date', N'DATE', CAST(0 AS BIT), N'Date the overtime was requested for', N'Date'),
        (N'analytics.vw_OvertimeRequest', N'hours', N'DECIMAL(4,2)', CAST(0 AS BIT), N'Amount of overtime hours requested', N'Hours'),
        (N'analytics.vw_OvertimeRequest', N'status', N'NVARCHAR(20)', CAST(0 AS BIT), N'Approval workflow status', N'Status'),
        (N'analytics.vw_OvertimeRequest', N'reason', N'NVARCHAR(500)', CAST(0 AS BIT), N'Business reason for extra hours', N'Reason'),
        (N'analytics.vw_OvertimeRequest', N'bu_id', N'INT', CAST(0 AS BIT), N'Business unit identifier for tenant scoping', N'Business Unit ID'),

        (N'analytics.vw_Scheduling', N'shift_id', N'INT', CAST(0 AS BIT), N'Unique shift record identifier', N'Shift ID'),
        (N'analytics.vw_Scheduling', N'agent_id', N'INT', CAST(0 AS BIT), N'Identifier of the assigned agent', N'Agent ID'),
        (N'analytics.vw_Scheduling', N'agent_name', N'NVARCHAR(150)', CAST(0 AS BIT), N'Agent full name', N'Agent Name'),
        (N'analytics.vw_Scheduling', N'team_name', N'NVARCHAR(100)', CAST(0 AS BIT), N'Operational team handling the shift', N'Team'),
        (N'analytics.vw_Scheduling', N'date', N'DATE', CAST(0 AS BIT), N'Date of the scheduled shift', N'Date'),
        (N'analytics.vw_Scheduling', N'shift_name', N'NVARCHAR(50)', CAST(0 AS BIT), N'Shift category name', N'Shift'),
        (N'analytics.vw_Scheduling', N'start_time', N'TIME', CAST(0 AS BIT), N'Shift start time', N'Start Time'),
        (N'analytics.vw_Scheduling', N'end_time', N'TIME', CAST(0 AS BIT), N'Shift end time', N'End Time'),
        (N'analytics.vw_Scheduling', N'queue_name', N'NVARCHAR(100)', CAST(1 AS BIT), N'Queue assigned for the shift', N'Queue'),
        (N'analytics.vw_Scheduling', N'bu_id', N'INT', CAST(0 AS BIT), N'Business unit identifier for tenant scoping', N'Business Unit ID')
    ) AS src(table_name, column_name, data_type, is_nullable, description, display_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM _metadata.catalog_columns AS target
        WHERE target.table_name = src.table_name
          AND target.column_name = src.column_name
    );

    UPDATE target
    SET join_type = src.join_type
    FROM _metadata.catalog_joins AS target
    INNER JOIN (VALUES
        (N'analytics.vw_PersonDetail', N'analytics.vw_AbsenceRequest', N'agent_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_AbsenceRequest', N'bu_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_OvertimeRequest', N'agent_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_OvertimeRequest', N'bu_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_Scheduling', N'agent_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_Scheduling', N'bu_id', N'INNER')
    ) AS src(source_table, target_table, join_column, join_type)
        ON target.source_table = src.source_table
       AND target.target_table = src.target_table
       AND target.join_column = src.join_column;

    INSERT INTO _metadata.catalog_joins (source_table, target_table, join_column, join_type)
    SELECT src.source_table, src.target_table, src.join_column, src.join_type
    FROM (VALUES
        (N'analytics.vw_PersonDetail', N'analytics.vw_AbsenceRequest', N'agent_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_AbsenceRequest', N'bu_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_OvertimeRequest', N'agent_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_OvertimeRequest', N'bu_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_Scheduling', N'agent_id', N'INNER'),
        (N'analytics.vw_PersonDetail', N'analytics.vw_Scheduling', N'bu_id', N'INNER')
    ) AS src(source_table, target_table, join_column, join_type)
    WHERE NOT EXISTS (
        SELECT 1
        FROM _metadata.catalog_joins AS target
        WHERE target.source_table = src.source_table
          AND target.target_table = src.target_table
          AND target.join_column = src.join_column
    );

    COMMIT TRAN;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRAN;
    THROW;
END CATCH;
