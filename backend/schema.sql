-- ============================================================
-- Gmail Follow-Up Automation System — Database Schema
-- ============================================================

CREATE DATABASE IF NOT EXISTS gmail_followup;
USE gmail_followup;

-- Users table: stores SaaS user accounts + Gmail OAuth tokens
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    -- Gmail OAuth2 token stored as JSON blob
    gmail_token TEXT,
    -- Whether auto follow-up is active for this user
    automation_enabled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Leads table: incoming inquiry emails detected from Gmail
CREATE TABLE IF NOT EXISTS leads (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    -- Sender email address
    from_email VARCHAR(255) NOT NULL,
    from_name VARCHAR(255),
    subject VARCHAR(500),
    -- Original email body (plain text)
    message TEXT,
    -- Gmail thread ID — used to reply in same thread
    thread_id VARCHAR(255),
    -- Gmail message ID — the specific message
    message_id VARCHAR(255),
    -- Status: new | replied | converted | unsubscribed
    status ENUM('new', 'replied', 'converted', 'unsubscribed') DEFAULT 'new',
    date_received TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Set to TRUE when the lead replies back (stops follow-ups)
    has_replied BOOLEAN DEFAULT FALSE,
    INDEX idx_user_id (user_id),
    INDEX idx_thread_id (thread_id),
    INDEX idx_status (status),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Follow-ups table: tracks scheduled and sent follow-up emails
CREATE TABLE IF NOT EXISTS followups (
    id INT AUTO_INCREMENT PRIMARY KEY,
    lead_id INT NOT NULL,
    -- Which follow-up sequence: 1 = Day 3, 2 = Day 5, 3 = Day 7
    followup_number TINYINT NOT NULL,
    scheduled_date TIMESTAMP NOT NULL,
    sent_date TIMESTAMP NULL,
    -- Status: pending | sent | skipped (lead replied) | failed
    status ENUM('pending', 'sent', 'skipped', 'failed') DEFAULT 'pending',
    -- AI-generated email body stored here
    generated_body TEXT,
    error_message TEXT,
    INDEX idx_lead_id (lead_id),
    INDEX idx_scheduled_date (scheduled_date),
    INDEX idx_status (status),
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
);
