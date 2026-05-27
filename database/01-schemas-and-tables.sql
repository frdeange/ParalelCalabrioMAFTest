SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRAN;

    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'wfm')
        EXEC(N'CREATE SCHEMA [wfm]');
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'absence')
        EXEC(N'CREATE SCHEMA [absence]');
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'overtime')
        EXEC(N'CREATE SCHEMA [overtime]');
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'scheduling')
        EXEC(N'CREATE SCHEMA [scheduling]');
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'_metadata')
        EXEC(N'CREATE SCHEMA [_metadata]');

    IF OBJECT_ID(N'wfm.business_unit', N'U') IS NULL
    BEGIN
        CREATE TABLE wfm.business_unit (
            bu_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_wfm_business_unit PRIMARY KEY,
            name NVARCHAR(100) NOT NULL,
            code NVARCHAR(20) NOT NULL,
            CONSTRAINT UQ_wfm_business_unit_code UNIQUE (code)
        );
    END;

    IF OBJECT_ID(N'wfm.site', N'U') IS NULL
    BEGIN
        CREATE TABLE wfm.site (
            site_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_wfm_site PRIMARY KEY,
            bu_id INT NOT NULL,
            name NVARCHAR(100) NOT NULL,
            timezone NVARCHAR(50) NOT NULL,
            CONSTRAINT FK_wfm_site_business_unit FOREIGN KEY (bu_id) REFERENCES wfm.business_unit (bu_id),
            CONSTRAINT UQ_wfm_site_bu_name UNIQUE (bu_id, name)
        );
    END;

    IF OBJECT_ID(N'wfm.team', N'U') IS NULL
    BEGIN
        CREATE TABLE wfm.team (
            team_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_wfm_team PRIMARY KEY,
            bu_id INT NOT NULL,
            name NVARCHAR(100) NOT NULL,
            manager_agent_id INT NULL,
            CONSTRAINT FK_wfm_team_business_unit FOREIGN KEY (bu_id) REFERENCES wfm.business_unit (bu_id),
            CONSTRAINT UQ_wfm_team_bu_name UNIQUE (bu_id, name),
            CONSTRAINT UQ_wfm_team_bu_team UNIQUE (bu_id, team_id)
        );
    END;

    IF OBJECT_ID(N'wfm.agent', N'U') IS NULL
    BEGIN
        CREATE TABLE wfm.agent (
            agent_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_wfm_agent PRIMARY KEY,
            bu_id INT NOT NULL,
            full_name NVARCHAR(150) NOT NULL,
            employee_id NVARCHAR(20) NOT NULL,
            team_id INT NOT NULL,
            site_id INT NOT NULL,
            hire_date DATE NOT NULL,
            termination_date DATE NULL,
            status NVARCHAR(20) NOT NULL CONSTRAINT DF_wfm_agent_status DEFAULT (N'Active'),
            CONSTRAINT FK_wfm_agent_business_unit FOREIGN KEY (bu_id) REFERENCES wfm.business_unit (bu_id),
            CONSTRAINT FK_wfm_agent_team FOREIGN KEY (bu_id, team_id) REFERENCES wfm.team (bu_id, team_id),
            CONSTRAINT FK_wfm_agent_site FOREIGN KEY (site_id) REFERENCES wfm.site (site_id),
            CONSTRAINT UQ_wfm_agent_bu_employee UNIQUE (bu_id, employee_id),
            CONSTRAINT UQ_wfm_agent_bu_agent UNIQUE (bu_id, agent_id)
        );
    END;

    IF OBJECT_ID(N'wfm.skill', N'U') IS NULL
    BEGIN
        CREATE TABLE wfm.skill (
            skill_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_wfm_skill PRIMARY KEY,
            bu_id INT NOT NULL,
            name NVARCHAR(100) NOT NULL,
            CONSTRAINT FK_wfm_skill_business_unit FOREIGN KEY (bu_id) REFERENCES wfm.business_unit (bu_id),
            CONSTRAINT UQ_wfm_skill_bu_name UNIQUE (bu_id, name)
        );
    END;

    IF OBJECT_ID(N'wfm.agent_skill', N'U') IS NULL
    BEGIN
        CREATE TABLE wfm.agent_skill (
            agent_id INT NOT NULL,
            skill_id INT NOT NULL,
            proficiency NVARCHAR(20) NOT NULL,
            CONSTRAINT PK_wfm_agent_skill PRIMARY KEY (agent_id, skill_id),
            CONSTRAINT FK_wfm_agent_skill_agent FOREIGN KEY (agent_id) REFERENCES wfm.agent (agent_id),
            CONSTRAINT FK_wfm_agent_skill_skill FOREIGN KEY (skill_id) REFERENCES wfm.skill (skill_id)
        );
    END;

    IF OBJECT_ID(N'absence.absence_type', N'U') IS NULL
    BEGIN
        CREATE TABLE absence.absence_type (
            absence_type_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_absence_type PRIMARY KEY,
            bu_id INT NOT NULL,
            name NVARCHAR(50) NOT NULL,
            paid BIT NOT NULL,
            CONSTRAINT FK_absence_type_business_unit FOREIGN KEY (bu_id) REFERENCES wfm.business_unit (bu_id),
            CONSTRAINT UQ_absence_type_bu_name UNIQUE (bu_id, name)
        );
    END;

    IF OBJECT_ID(N'absence.absence_request', N'U') IS NULL
    BEGIN
        CREATE TABLE absence.absence_request (
            request_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_absence_request PRIMARY KEY,
            bu_id INT NOT NULL,
            agent_id INT NOT NULL,
            absence_type_id INT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            status NVARCHAR(20) NOT NULL,
            requested_at DATETIME2(0) NOT NULL,
            decided_by NVARCHAR(150) NULL,
            decided_at DATETIME2(0) NULL,
            CONSTRAINT FK_absence_request_business_unit FOREIGN KEY (bu_id) REFERENCES wfm.business_unit (bu_id),
            CONSTRAINT FK_absence_request_agent FOREIGN KEY (bu_id, agent_id) REFERENCES wfm.agent (bu_id, agent_id),
            CONSTRAINT FK_absence_request_type FOREIGN KEY (absence_type_id) REFERENCES absence.absence_type (absence_type_id)
        );
    END;

    IF OBJECT_ID(N'overtime.overtime_request', N'U') IS NULL
    BEGIN
        CREATE TABLE overtime.overtime_request (
            ot_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_overtime_request PRIMARY KEY,
            bu_id INT NOT NULL,
            agent_id INT NOT NULL,
            [date] DATE NOT NULL,
            hours DECIMAL(4,2) NOT NULL,
            status NVARCHAR(20) NOT NULL,
            reason NVARCHAR(500) NOT NULL,
            requested_at DATETIME2(0) NOT NULL,
            approved_by NVARCHAR(150) NULL,
            approved_at DATETIME2(0) NULL,
            CONSTRAINT FK_overtime_request_business_unit FOREIGN KEY (bu_id) REFERENCES wfm.business_unit (bu_id),
            CONSTRAINT FK_overtime_request_agent FOREIGN KEY (bu_id, agent_id) REFERENCES wfm.agent (bu_id, agent_id)
        );
    END;

    IF OBJECT_ID(N'scheduling.shift', N'U') IS NULL
    BEGIN
        CREATE TABLE scheduling.shift (
            shift_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_scheduling_shift PRIMARY KEY,
            bu_id INT NOT NULL,
            agent_id INT NOT NULL,
            [date] DATE NOT NULL,
            shift_name NVARCHAR(50) NOT NULL,
            start_time TIME(0) NOT NULL,
            end_time TIME(0) NOT NULL,
            queue_name NVARCHAR(100) NULL,
            CONSTRAINT FK_scheduling_shift_business_unit FOREIGN KEY (bu_id) REFERENCES wfm.business_unit (bu_id),
            CONSTRAINT FK_scheduling_shift_agent FOREIGN KEY (bu_id, agent_id) REFERENCES wfm.agent (bu_id, agent_id)
        );
    END;

    IF OBJECT_ID(N'_metadata.catalog_tables', N'U') IS NULL
    BEGIN
        CREATE TABLE _metadata.catalog_tables (
            table_name NVARCHAR(128) NOT NULL CONSTRAINT PK_catalog_tables PRIMARY KEY,
            schema_name NVARCHAR(128) NOT NULL,
            description NVARCHAR(500) NOT NULL,
            keywords NVARCHAR(500) NOT NULL,
            is_active BIT NOT NULL CONSTRAINT DF_catalog_tables_is_active DEFAULT (1)
        );
    END;

    IF OBJECT_ID(N'_metadata.catalog_columns', N'U') IS NULL
    BEGIN
        CREATE TABLE _metadata.catalog_columns (
            table_name NVARCHAR(128) NOT NULL,
            column_name NVARCHAR(128) NOT NULL,
            data_type NVARCHAR(50) NOT NULL,
            is_nullable BIT NOT NULL,
            description NVARCHAR(500) NOT NULL,
            display_name NVARCHAR(100) NULL,
            CONSTRAINT PK_catalog_columns PRIMARY KEY (table_name, column_name),
            CONSTRAINT FK_catalog_columns_catalog_tables FOREIGN KEY (table_name) REFERENCES _metadata.catalog_tables (table_name)
        );
    END;

    IF OBJECT_ID(N'_metadata.catalog_joins', N'U') IS NULL
    BEGIN
        CREATE TABLE _metadata.catalog_joins (
            source_table NVARCHAR(128) NOT NULL,
            target_table NVARCHAR(128) NOT NULL,
            join_column NVARCHAR(128) NOT NULL,
            join_type NVARCHAR(20) NOT NULL CONSTRAINT DF_catalog_joins_join_type DEFAULT (N'INNER'),
            CONSTRAINT PK_catalog_joins PRIMARY KEY (source_table, target_table, join_column)
        );
    END;

    IF OBJECT_ID(N'wfm.team', N'U') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM sys.key_constraints WHERE name = N'UQ_wfm_team_bu_team')
    BEGIN
        ALTER TABLE wfm.team
            ADD CONSTRAINT UQ_wfm_team_bu_team UNIQUE (bu_id, team_id);
    END;

    IF OBJECT_ID(N'wfm.agent', N'U') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM sys.key_constraints WHERE name = N'UQ_wfm_agent_bu_agent')
    BEGIN
        ALTER TABLE wfm.agent
            ADD CONSTRAINT UQ_wfm_agent_bu_agent UNIQUE (bu_id, agent_id);
    END;

    IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_wfm_agent_team')
    BEGIN
        ALTER TABLE wfm.agent DROP CONSTRAINT FK_wfm_agent_team;
    END;

    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_wfm_agent_team')
    BEGIN
        ALTER TABLE wfm.agent
            ADD CONSTRAINT FK_wfm_agent_team
            FOREIGN KEY (bu_id, team_id) REFERENCES wfm.team (bu_id, team_id);
    END;

    IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_wfm_team_manager_agent')
    BEGIN
        ALTER TABLE wfm.team DROP CONSTRAINT FK_wfm_team_manager_agent;
    END;

    IF COL_LENGTH(N'wfm.team', N'manager_agent_id') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_wfm_team_manager_agent')
    BEGIN
        ALTER TABLE wfm.team
            ADD CONSTRAINT FK_wfm_team_manager_agent
            FOREIGN KEY (bu_id, manager_agent_id) REFERENCES wfm.agent (bu_id, agent_id);
    END;

    IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_absence_request_agent')
    BEGIN
        ALTER TABLE absence.absence_request DROP CONSTRAINT FK_absence_request_agent;
    END;

    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_absence_request_agent')
    BEGIN
        ALTER TABLE absence.absence_request
            ADD CONSTRAINT FK_absence_request_agent
            FOREIGN KEY (bu_id, agent_id) REFERENCES wfm.agent (bu_id, agent_id);
    END;

    IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_overtime_request_agent')
    BEGIN
        ALTER TABLE overtime.overtime_request DROP CONSTRAINT FK_overtime_request_agent;
    END;

    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_overtime_request_agent')
    BEGIN
        ALTER TABLE overtime.overtime_request
            ADD CONSTRAINT FK_overtime_request_agent
            FOREIGN KEY (bu_id, agent_id) REFERENCES wfm.agent (bu_id, agent_id);
    END;

    IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_scheduling_shift_agent')
    BEGIN
        ALTER TABLE scheduling.shift DROP CONSTRAINT FK_scheduling_shift_agent;
    END;

    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_scheduling_shift_agent')
    BEGIN
        ALTER TABLE scheduling.shift
            ADD CONSTRAINT FK_scheduling_shift_agent
            FOREIGN KEY (bu_id, agent_id) REFERENCES wfm.agent (bu_id, agent_id);
    END;

    COMMIT TRAN;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRAN;
    THROW;
END CATCH;
