# Requirements Document

## Introduction

Token Budget Platform is a public, searchable, community-contributed benchmarking product analogous to levels.fyi for organizational AI and large-language-model resource allowances. In this specification, “token budget” means an approved quantity of model input or output tokens available to a person, team, workload, or organization during a defined reporting period, with optional actual usage and cost information. The product does not cover cryptocurrency holdings, compensation, authentication tokens, or model context-window specifications.

The initial product enables visitors to discover how organizations allocate AI token capacity, compare like-for-like records, inspect provenance and confidence signals, and contribute privacy-preserving records. Moderators protect data quality through review, evidence handling, dispute resolution, and abuse controls. The product must run reproducibly on a local developer machine and be deployable to AWS; implementation technology selection is deferred to the design document.

## Glossary

- **Token_Budget_Platform**: The complete web product described by this specification.
- **Token**: A model-provider metering unit for text or multimodal input or output processed by an AI model.
- **Token_Budget**: An approved maximum number of Tokens available to a defined scope during a Reporting_Period.
- **Token_Usage**: The measured number of Tokens consumed by a defined scope during a Reporting_Period.
- **Reporting_Period**: A start date, end date, and timezone that bound a Token_Budget_Record.
- **Token_Budget_Record**: A structured contribution describing a Token_Budget and optional Token_Usage, cost, and contextual fields.
- **Publication_State**: One of Draft, Queued, Changes_Requested, Approved, Rejected, or Withdrawn; the lifecycle state controlling whether a Token_Budget_Record is eligible for public display independently of Verification_Status.
- **Organization_Profile**: A public page that groups publishable Token_Budget_Records for one named organization.
- **Contributor**: A person who creates or manages a Token_Budget_Record.
- **Public_Visitor**: A person who uses public product functions without authentication.
- **Moderator**: An authorized person who reviews submissions, reports, evidence, and disputes.
- **Administrator**: An authorized person who manages Moderator access and product configuration.
- **Model_Provider**: The company or service that supplies an AI model.
- **AI_Product**: A named application or service through which Tokens are allocated or consumed.
- **Workload_Category**: A controlled classification of AI activity, such as software development, customer support, research, or content generation.
- **Budget_Scope**: The population covered by a Token_Budget: individual, team, workload, or organization.
- **Organization_Size_Band**: A controlled range representing an organization’s employee count.
- **Geographic_Region**: A controlled country or regional classification associated with a Token_Budget_Record.
- **Normalized_Metric**: A derived Token quantity expressed per seat per 30 days or per workload execution.
- **Comparison_Set**: A collection of two through five Token_Budget_Records selected for side-by-side comparison.
- **Cost_Basis**: The unit or accounting interpretation used for a submitted monetary amount, such as per Reporting_Period, per seat, or per workload execution.
- **Source_Date**: The calendar date on which the Contributor obtained or last verified the submitted source information.
- **Evidence**: A private file, private link, or structured explanation supplied to support a Token_Budget_Record.
- **Security_Event**: A sanitized record of a security-relevant outcome, such as malicious-content scanning or an authorization failure.
- **Rate_Limit**: A configured maximum number of requests from one network source during a defined window, including the retry interval returned after excess requests.
- **Required_Backup**: A production backup designated by the published recovery policy as necessary to maintain the stated recovery objectives.
- **Verification_Status**: One of Unverified, Evidence_Submitted, Verified, Disputed, or Rejected, representing review state.
- **Moderation_Case**: A tracked review of a submission, community report, or dispute.
- **Community_Report**: A structured claim that published information is inaccurate, outdated, sensitive, or abusive.
- **Sensitive_Data**: Credentials, secrets, private keys, financial-account data, government identifiers, or non-public Evidence.
- **Personal_Data**: Information that identifies or can reasonably be linked to a natural person.
- **Audit_Record**: An immutable record of a privileged action, actor, timestamp, target, and outcome.
- **Local_Environment**: A developer-controlled runtime on a supported workstation without a required connection to paid cloud services.
- **AWS_Environment**: A deployed runtime hosted in an AWS account.
- **Deployment_Package**: Version-controlled application configuration and infrastructure definitions required to create an AWS_Environment.
- **Health_Check**: A machine-readable indication of application readiness and dependency health.
- **Service_Month**: One calendar month measured in Coordinated Universal Time.
- **Accessible_Interface**: A user interface conforming to Web Content Accessibility Guidelines 2.2 Level AA.
- **Submission_Management_Link**: A private, unguessable URL that allows a Contributor to view status, amend, or withdraw one submission without public identity disclosure.
- **Monthly_Availability**: The percentage of minutes in a Service_Month during which public read requests complete successfully, excluding announced maintenance of no more than four hours per Service_Month.

## Requirements

### Requirement 1: Browse and Understand the Dataset

**User Story:** As a Public_Visitor, I want to browse token-budget benchmarks, so that I can understand how organizations allocate AI capacity.

#### Acceptance Criteria

1. THE Token_Budget_Platform SHALL publish a scope explanation that distinguishes Token_Budgets from cryptocurrency holdings, compensation, authentication tokens, and model context-window specifications.
2. WHEN a Public_Visitor opens the public landing page, THE Token_Budget_Platform SHALL display the count of published Token_Budget_Records and the most recent dataset update date.
3. WHEN a Public_Visitor browses published data, THE Token_Budget_Platform SHALL display Organization_Profile, Model_Provider, AI_Product, Workload_Category, Budget_Scope, Token_Budget, Reporting_Period, Geographic_Region, and Verification_Status values available for each Token_Budget_Record.
4. WHEN a Public_Visitor browses published data, THE Token_Budget_Platform SHALL display the documented field headings regardless of the number of matching Token_Budget_Records.
5. WHEN more than 25 Token_Budget_Records match a browse request, THE Token_Budget_Platform SHALL paginate the matching records in pages of at most 25 records.
6. IF no Token_Budget_Records are approved for public display, THEN THE Token_Budget_Platform SHALL display an empty-state explanation and a submission action.

### Requirement 2: Search, Filter, and Sort Records

**User Story:** As a Public_Visitor, I want to find relevant token-budget records, so that I can evaluate benchmarks matching my context.

#### Acceptance Criteria

1. WHEN a Public_Visitor submits an organization, Model_Provider, or AI_Product search term, THE Token_Budget_Platform SHALL return published Token_Budget_Records containing the case-insensitive term in the corresponding searchable fields.
2. WHEN a Public_Visitor selects one or more filters, THE Token_Budget_Platform SHALL restrict results by Organization_Size_Band, Geographic_Region, Workload_Category, Budget_Scope, Model_Provider, AI_Product, Verification_Status, and Reporting_Period as selected.
3. WHEN a Public_Visitor selects a sort option, THE Token_Budget_Platform SHALL order results by Token_Budget, Normalized_Metric, Reporting_Period end date, or Verification_Status as selected.
4. WHEN a Public_Visitor changes search, filter, sort, or page state, THE Token_Budget_Platform SHALL encode the resulting state in a shareable URL.
5. WHEN a search or filter request completes, THE Token_Budget_Platform SHALL display the matching record count.
6. IF a search or filter request has no matches, THEN THE Token_Budget_Platform SHALL display an empty-state explanation and an action to clear the active criteria.

### Requirement 3: Compare Token Budgets

**User Story:** As a Public_Visitor, I want to compare selected token budgets, so that I can identify meaningful differences between organizations and use cases.

#### Acceptance Criteria

1. WHEN a Public_Visitor selects between two and five distinct published Token_Budget_Records, THE Token_Budget_Platform SHALL create a Comparison_Set.
2. WHEN a Comparison_Set is displayed, THE Token_Budget_Platform SHALL align the selected records by Budget_Scope, Reporting_Period, Model_Provider, AI_Product, Workload_Category, Geographic_Region, Token_Budget, Token_Usage, cost amount and Cost_Basis, and Verification_Status.
3. WHEN a Token_Budget_Record contains the inputs required for a Normalized_Metric, THE Token_Budget_Platform SHALL display the Normalized_Metric with the calculation formula and source values.
4. IF a Token_Budget_Record lacks an input required for a Normalized_Metric, THEN THE Token_Budget_Platform SHALL label the Normalized_Metric unavailable and identify each missing input.
5. WHEN Token_Budget_Records use different Reporting_Period lengths, THE Token_Budget_Platform SHALL present both original values and 30-day normalized values.
6. WHEN a Public_Visitor changes a Comparison_Set, THE Token_Budget_Platform SHALL encode the selected record identifiers in a shareable URL.

### Requirement 4: Inspect Organization and Record Details

**User Story:** As a Public_Visitor, I want to inspect record context and provenance, so that I can judge whether a benchmark applies to my decision.

#### Acceptance Criteria

1. WHEN a Public_Visitor opens an Organization_Profile, THE Token_Budget_Platform SHALL display the organization name, industry, Organization_Size_Band, Geographic_Region, and published Token_Budget_Records available for the organization.
2. WHEN a Public_Visitor opens a Token_Budget_Record, THE Token_Budget_Platform SHALL display every publishable submitted field, Verification_Status, publication date, last-reviewed date, and methodology notes.
3. WHEN at least three published Token_Budget_Records share an organization and comparison dimension, THE Token_Budget_Platform SHALL display the median, minimum, maximum, and record count for that group.
4. IF fewer than three published Token_Budget_Records share an organization and comparison dimension, THEN THE Token_Budget_Platform SHALL suppress aggregate statistics for that group and display the minimum-group-size explanation.
5. WHEN a Token_Budget_Record crosses 12 calendar months after the Reporting_Period end date, THE Token_Budget_Platform SHALL automatically label the record as stale without requiring Moderator action.
6. WHEN a published Token_Budget_Record has a Community_Report under review, THE Token_Budget_Platform SHALL display the Disputed Verification_Status until the Moderation_Case is resolved and SHALL remove the Disputed status immediately when the case is resolved.

### Requirement 5: Submit Token-Budget Information

**User Story:** As a Contributor, I want to submit token-budget information without exposing my identity, so that I can improve the benchmark dataset safely.

#### Acceptance Criteria

1. WHEN a Contributor starts a submission, THE Token_Budget_Platform SHALL collect organization, industry, Organization_Size_Band, Geographic_Region, Budget_Scope, the applicable seat count for individual, team, or organization scope or workload count for workload scope, Model_Provider, AI_Product, Workload_Category, Token_Budget, input-versus-output allocation when known, Reporting_Period, and Source_Date.
2. WHERE optional usage reporting is selected, THE Token_Budget_Platform SHALL collect Token_Usage for the Reporting_Period.
3. WHERE optional cost reporting is selected, THE Token_Budget_Platform SHALL collect the monetary amount, currency code, and Cost_Basis for the Reporting_Period.
4. WHEN a Contributor enters a submission field, THE Token_Budget_Platform SHALL validate the field against documented type, range, and format rules before submission.
5. IF a required submission field is missing or invalid, THEN THE Token_Budget_Platform SHALL identify the field and provide a corrective message.
6. WHEN all required submission fields are valid, THE Token_Budget_Platform SHALL present a preview containing the exact information eligible for public display.
7. WHEN a Contributor confirms a valid preview successfully, THE Token_Budget_Platform SHALL create one Token_Budget_Record with the Unverified Verification_Status.
8. WHEN confirmation succeeds, THE Token_Budget_Platform SHALL provide a non-public reference identifier for tracking the submission.
9. WHERE anonymous public attribution is selected, THE Token_Budget_Platform SHALL publish the Contributor attribution as “Anonymous Contributor.”
10. IF a new submission matches an existing organization, Budget_Scope, AI_Product, Token_Budget, and Reporting_Period, THEN THE Token_Budget_Platform SHALL flag the new submission for duplicate review.

### Requirement 6: Supply Evidence and Confidence Signals

**User Story:** As a Contributor, I want to support a submission with evidence, so that visitors can distinguish stronger records from unsupported claims.

#### Acceptance Criteria

1. WHERE Evidence submission is selected, THE Token_Budget_Platform SHALL accept a private file, private link, or structured explanation associated with one Token_Budget_Record.
2. WHEN a Contributor submits Evidence, THE Token_Budget_Platform SHALL display the Evidence to authorized Moderators only.
3. WHEN a valid Evidence item is successfully accepted for an Unverified Token_Budget_Record, THE Token_Budget_Platform SHALL assign the Evidence_Submitted Verification_Status.
4. IF an Evidence file exceeds 10 megabytes or uses a file type outside the documented allowlist, THEN THE Token_Budget_Platform SHALL reject the file with a corrective message while preserving the associated Evidence submission opportunity.
5. WHEN a Public_Visitor views a published Token_Budget_Record, THE Token_Budget_Platform SHALL display the Verification_Status and a plain-language definition of the status.
6. WHEN a Moderator confirms successfully accepted Evidence for a Token_Budget_Record, THE Token_Budget_Platform SHALL assign the Verified Verification_Status based on the Moderator confirmation.

7. WHEN a Moderator rejects an Evidence verification request, THE Token_Budget_Platform SHALL assign the Rejected Verification_Status and record a reason visible to the Contributor.

### Requirement 7: Moderate Submissions and Disputes

**User Story:** As a Moderator, I want a traceable review process, so that published benchmarks remain trustworthy and harmful content is contained.

#### Acceptance Criteria

1. WHEN confirmation succeeds, THE Token_Budget_Platform SHALL place the Token_Budget_Record in a moderation queue.
2. WHEN a Moderator reviews a queued Token_Budget_Record, THE Token_Budget_Platform SHALL allow approval, rejection, or a request for Contributor changes.
3. WHEN a Moderator approves a Token_Budget_Record, THE Token_Budget_Platform SHALL set its Publication_State to Approved and make the record eligible for public display with the current Verification_Status, provided the record is not withdrawn or suppressed.
4. WHEN a Moderator rejects a Token_Budget_Record, THE Token_Budget_Platform SHALL exclude the rejected record from public search, comparison, and Organization_Profile results.
5. WHEN a Moderator requests Contributor changes, THE Token_Budget_Platform SHALL communicate the requested fields and review reason through the Submission_Management_Link.
6. WHEN a Moderator identifies a discrepancy in a submitted Token_Budget, Token_Usage, or cost value, THE Token_Budget_Platform SHALL request a Contributor amendment to the value.
7. WHEN a Public_Visitor submits a Community_Report with a category and explanation, THE Token_Budget_Platform SHALL create a Moderation_Case linked to the reported Token_Budget_Record.
8. WHEN a Community_Report alleges exposure of Personal_Data or Sensitive_Data, THE Token_Budget_Platform SHALL remove the reported content from public display before returning a successful report response and keep the content suppressed pending Moderator review.
9. WHEN a Moderator resolves a Moderation_Case, THE Token_Budget_Platform SHALL record the decision, reason, timestamp, and resulting Verification_Status.
10. WHEN an authorized Moderator or Administrator performs a privileged action, THE Token_Budget_Platform SHALL create an Audit_Record.
11. IF Audit_Record creation fails for an authorized privileged action, THEN THE Token_Budget_Platform SHALL complete the privileged action.
12. IF Audit_Record creation fails for an authorized privileged action, THEN THE Token_Budget_Platform SHALL record a sanitized operational error.
13. IF a person without the Moderator or Administrator role requests moderation functions, THEN THE Token_Budget_Platform SHALL deny the request and record the authorization failure.

### Requirement 8: Manage Contributor Submissions

**User Story:** As a Contributor, I want to track and control my submission, so that I can correct or withdraw contributed information.

#### Acceptance Criteria

1. WHEN confirmation succeeds, THE Token_Budget_Platform SHALL issue a Submission_Management_Link to the Contributor.
2. WHEN a Contributor opens a valid Submission_Management_Link, THE Token_Budget_Platform SHALL display the submission, Verification_Status, moderation feedback, and publication state.
3. WHEN a Contributor opens a valid Submission_Management_Link for a withdrawn submission, THE Token_Budget_Platform SHALL display the withdrawn submission to the Contributor.
4. WHEN a Contributor amends a submitted benchmark value, THE Token_Budget_Platform SHALL return the Token_Budget_Record to the moderation queue.
5. WHEN a Contributor withdraws a submission, THE Token_Budget_Platform SHALL remove the associated Token_Budget_Record from public display within 24 hours.
6. IF a Submission_Management_Link is invalid or expired, THEN THE Token_Budget_Platform SHALL deny submission access, display a non-enumerating recovery action, and avoid revealing whether the submission exists.
7. WHERE email recovery is selected, THE Token_Budget_Platform SHALL send a replacement Submission_Management_Link only to the email address previously associated with the submission and SHALL return a non-enumerating response.
8. WHEN a Contributor requests deletion of associated Personal_Data, THE Token_Budget_Platform SHALL complete deletion or irreversible anonymization within 30 calendar days.

### Requirement 9: Protect Privacy and Sensitive Information

**User Story:** As a Contributor, I want privacy-preserving publication and retention controls, so that contributing does not expose personal or confidential information.

#### Acceptance Criteria

1. THE Token_Budget_Platform SHALL limit required Personal_Data collection to information necessary for submission management, moderation, security, and legal obligations.
2. WHERE email notifications or recovery are selected, THE Token_Budget_Platform SHALL collect one Contributor email address as non-public Personal_Data.
3. WHEN a Token_Budget_Record is published, THE Token_Budget_Platform SHALL exclude Contributor contact details, Submission_Management_Link values, Evidence, exact internal team names, and free-text Personal_Data from public output.
4. WHEN a Contributor enters free text, THE Token_Budget_Platform SHALL warn the Contributor to exclude Personal_Data, Sensitive_Data, and confidential organization information.
5. WHEN a Moderator confirms Personal_Data or Sensitive_Data exposure, THE Token_Budget_Platform SHALL redact the exposed content from every public representation of the Token_Budget_Record.
6. THE Token_Budget_Platform SHALL publish a retention schedule covering submissions, Evidence, Personal_Data, Moderation_Cases, backups, and Audit_Records.
7. WHEN Evidence reaches 365 days after final Moderation_Case resolution, THE Token_Budget_Platform SHALL delete the Evidence unless a documented legal hold applies.
8. WHEN operational logs are created, THE Token_Budget_Platform SHALL redact credentials, Submission_Management_Link values, Personal_Data, and Evidence content.
9. WHEN a Public_Visitor views aggregate statistics, THE Token_Budget_Platform SHALL disclose the aggregation dimensions, contributing record count, and minimum-group-size rule.

### Requirement 10: Enforce Application Security

**User Story:** As an Administrator, I want layered security controls, so that public contributions and privileged functions resist common abuse.

#### Acceptance Criteria

1. WHEN data crosses an external network boundary, THE Token_Budget_Platform SHALL protect the data with Transport Layer Security version 1.2 or later.
2. WHEN Personal_Data, Sensitive_Data, or Evidence is stored, THE Token_Budget_Platform SHALL protect the stored data with encryption using keys controlled by the hosting environment owner, with owner-controlled keys in the AWS_Environment and a developer-controlled key stored outside version control in the Local_Environment.
3. WHEN a Moderator or Administrator authenticates, THE Token_Budget_Platform SHALL require multi-factor authentication.
4. WHEN an authenticated person requests a protected operation, THE Token_Budget_Platform SHALL authorize the operation against the person’s assigned role and requested resource.
5. IF five failed authentication attempts target one privileged account within 15 minutes, THEN THE Token_Budget_Platform SHALL suspend further authentication attempts for that account for 15 minutes.
6. WHEN the Token_Budget_Platform accepts text input, THE Token_Budget_Platform SHALL neutralize executable markup and reject input that violates documented length or format constraints.
7. WHEN the Token_Budget_Platform accepts an Evidence file, THE Token_Budget_Platform SHALL block Moderator access until malicious-content scanning finishes.
8. WHEN malicious-content scanning finishes, THE Token_Budget_Platform SHALL create a Security_Event containing the scan success or failure outcome.
9. IF an Evidence file fails malicious-content scanning, THEN THE Token_Budget_Platform SHALL retain the Evidence file in quarantine.
10. IF one network source exceeds the configured Rate_Limit for submissions or Community_Reports, THEN THE Token_Budget_Platform SHALL reject excess requests with a positive retry interval consistent with the configured window.
11. THE Token_Budget_Platform SHALL retain Audit_Records for at least 365 days.

### Requirement 11: Provide an Accessible and Responsive Experience

**User Story:** As a Public_Visitor using assistive technology or a constrained device, I want equivalent access to every core journey, so that I can use the benchmark independently.

#### Acceptance Criteria

1. THE Token_Budget_Platform SHALL provide an Accessible_Interface for browse, search, filter, compare, detail, submission, submission management, and Community_Report journeys.
2. WHEN a person uses only a keyboard, THE Token_Budget_Platform SHALL expose every interactive control in a logical focus order with a visible focus indicator.
3. WHEN a validation error occurs, THE Token_Budget_Platform SHALL associate a text error message with the affected field and move focus to an error summary.
4. WHEN a chart or visual comparison is displayed, THE Token_Budget_Platform SHALL provide the same data in an accessible tabular or textual form.
5. WHEN content is displayed at viewport widths from 320 through 1920 CSS pixels, THE Token_Budget_Platform SHALL preserve content access without two-dimensional scrolling except for data tables.
6. WHEN status is conveyed visually, THE Token_Budget_Platform SHALL provide a non-color text or icon indicator with an accessible name.
7. WHEN a Public_Visitor requests public content in a supported browser, THE Token_Budget_Platform SHALL provide a unique page title, one primary heading, and descriptive link text.

### Requirement 12: Govern Comparable Data

**User Story:** As a Moderator, I want consistent classifications and correction history, so that comparisons remain meaningful as the dataset grows.

#### Acceptance Criteria

1. WHEN an Administrator updates controlled values for Model_Provider, AI_Product, Workload_Category, Organization_Size_Band, or Geographic_Region, THE Token_Budget_Platform SHALL apply the new values to subsequent submissions.
2. WHEN a submitted organization name matches a documented organization alias, THE Token_Budget_Platform SHALL associate the Token_Budget_Record with the canonical Organization_Profile.
3. WHEN a Moderator merges duplicate Organization_Profiles, THE Token_Budget_Platform SHALL preserve every associated Token_Budget_Record and Audit_Record under the canonical Organization_Profile.
4. WHEN a published Token_Budget_Record is amended, THE Token_Budget_Platform SHALL preserve a non-public version history containing changed fields, timestamps, and change reasons.
5. WHEN a material benchmark value changes after publication, THE Token_Budget_Platform SHALL display the latest value and a public correction date.
6. WHEN a Moderator records a correction event without changing a benchmark value, THE Token_Budget_Platform SHALL permit display of the correction date.
7. WHEN a Normalized_Metric is calculated, THE Token_Budget_Platform SHALL use one documented calculation rule for all Token_Budget_Records with the same Budget_Scope.
8. WHEN a calculation rule changes, THE Token_Budget_Platform SHALL recalculate affected Normalized_Metrics and display the active methodology version.

### Requirement 13: Support Local Development and Debugging

**User Story:** As a developer, I want a reproducible local environment, so that I can implement, debug, and verify changes without paid cloud dependencies.

#### Acceptance Criteria

1. WHEN a developer follows the documented prerequisites on Windows 11, a current macOS release, or the current Ubuntu long-term-support release, THE Local_Environment SHALL start through one documented command.
2. WHEN the Local_Environment starts, THE Token_Budget_Platform SHALL provide public, Contributor, Moderator, and Administrator journeys using local dependencies or test doubles.
3. WHEN the Local_Environment starts, THE Token_Budget_Platform SHALL provide all core journeys without a connection to a paid cloud service.
4. WHEN a developer loads documented sample data, THE Local_Environment SHALL contain at least 20 Token_Budget_Records spanning at least three organizations, three Workload_Categories, and every Verification_Status.
5. WHEN a developer runs the documented reset command, THE Local_Environment SHALL return application data and local dependencies to the documented baseline state.
6. WHEN a developer attaches a debugger by following the local development documentation, THE Local_Environment SHALL expose original application source locations for server-side and browser-side failures.
7. IF required local configuration is missing, THEN THE Local_Environment SHALL identify each missing setting and provide a reference to the documented example configuration.
8. THE Local_Environment SHALL store developer-specific secrets outside version-controlled files.

### Requirement 14: Automate Verification

**User Story:** As a developer, I want deterministic automated verification, so that I can detect regressions before deployment.

#### Acceptance Criteria

1. WHEN a developer runs the documented verification command, THE Token_Budget_Platform SHALL execute unit, integration, and end-to-end test suites in a single finite run.
2. WHEN automated tests run, THE Token_Budget_Platform SHALL use isolated test data and repeatable dependency substitutes.
3. WHEN automated tests run without AWS credentials, THE Token_Budget_Platform SHALL complete every test except tests explicitly marked as requiring AWS integration.
4. WHEN comparison or Normalized_Metric logic is tested, THE Token_Budget_Platform SHALL include generated-input tests covering valid ranges, missing optional values, boundary values, and invariant preservation.
5. WHEN submission validation is tested, THE Token_Budget_Platform SHALL include generated invalid inputs and verify a corrective error for every rejected input.
6. WHEN the Accessible_Interface is tested, THE Token_Budget_Platform SHALL run automated accessibility checks for each core journey and document the manual checks required for Web Content Accessibility Guidelines 2.2 Level AA.
7. WHERE automated verification is enabled, WHEN a change is proposed for deployment, THE Token_Budget_Platform SHALL require successful automated verification, static analysis, dependency vulnerability scanning, and secret scanning.
8. IF any required verification check fails, THEN THE Token_Budget_Platform SHALL block creation of a releasable deployment artifact and report the failed check.

### Requirement 15: Deploy Reproducibly to AWS

**User Story:** As an operator, I want a reproducible AWS deployment, so that I can host the product without maintaining an environment that differs from local development.

#### Acceptance Criteria

1. WHEN an operator provides a documented AWS account, region, domain, and deployment configuration, THE Deployment_Package SHALL create or update an AWS_Environment through one documented command.
2. THE Deployment_Package SHALL represent application runtime, network boundaries, data stores, file storage, encryption keys, access policies, monitoring, backups, and public routing in version control.
3. WHEN the same application source revision runs in a Local_Environment and an AWS_Environment, THE Token_Budget_Platform SHALL apply the same validation, comparison, Normalized_Metric, moderation-state, and privacy rules.
4. WHEN environment-specific values differ, THE Token_Budget_Platform SHALL obtain the values from external configuration without source-code modification.
5. WHEN an AWS_Environment deployment completes, THE Deployment_Package SHALL output the application endpoint, deployment version, and Health_Check endpoint.
6. WHEN an application deployment fails its Health_Check, THE Deployment_Package SHALL restore the previously healthy application version.
7. WHEN an operator creates separate development, staging, and production AWS_Environments, THE Deployment_Package SHALL isolate data, secrets, access policies, and public endpoints between the environments.
8. WHEN an operator removes an AWS_Environment, THE Deployment_Package SHALL retain production data backups according to the published retention schedule, regardless of whether the removal request includes complete decommission documentation.
9. IF a deployment configuration grants a public network path to Evidence or Personal_Data storage, THEN THE Deployment_Package SHALL reject the configuration before resource creation.

### Requirement 16: Meet Operational Service Expectations

**User Story:** As an operator, I want measurable health, performance, recovery, and alerting behavior, so that I can operate the public service reliably.

#### Acceptance Criteria

1. WHILE the production AWS_Environment is in service, THE Token_Budget_Platform SHALL achieve at least 99.5 percent Monthly_Availability.
2. WHILE the production dataset contains up to 1,000,000 published Token_Budget_Records and receives up to 100 public read requests per second, THE Token_Budget_Platform SHALL complete at least 95 percent of search and detail requests within 2 seconds measured at the server boundary.
3. WHEN the application runtime is ready to serve requests, THE Health_Check SHALL return readiness, deployment version, and dependency status without Personal_Data or Sensitive_Data.
4. IF five consecutive Health_Check attempts fail at one-minute intervals, THEN THE Token_Budget_Platform SHALL notify the configured operator channel within 5 minutes after the fifth failure.
5. WHEN an unhandled application error occurs, THE Token_Budget_Platform SHALL record a timestamp, correlation identifier, deployment version, operation name, and sanitized error category.
6. WHEN a Service_Month ends, THE Token_Budget_Platform SHALL produce Monthly_Availability, request volume, latency percentile, error-rate, moderation-queue age, and storage-growth metrics for that Service_Month.
7. WHILE production data is stored in the AWS_Environment, THE Token_Budget_Platform SHALL create Required_Backups on a schedule that supports a maximum data-loss interval of 24 hours.
8. IF a Required_Backup fails, THEN THE Token_Budget_Platform SHALL suspend production data-changing operations until a Required_Backup succeeds, while allowing public reads and Health_Check requests to continue.
9. WHEN an operator initiates a documented production recovery, THE Token_Budget_Platform SHALL restore service from a valid backup and pass the documented recovery Health_Check within 4 hours of recovery initiation.
10. WHEN planned maintenance affects public access, THE Token_Budget_Platform SHALL publish notice at least 48 hours before the maintenance start time.
11. IF a public request cannot complete, THEN THE Token_Budget_Platform SHALL return a safe error response containing a correlation identifier and a retryable-or-final classification.
