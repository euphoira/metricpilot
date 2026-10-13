# MetricPilot implementation contract

Standalone Python package `metricpilot`, localhost default port 8011. Light off-white / blue
data studio dashboard. No dependency on TriageFlow. JSON API; user/model text rendered safely.

GET /api/config -> {mode:'demo'|'live',model,limits:{max_csv_bytes,max_rows,max_query_rows,max_steps}}
GET /api/datasets -> list of Dataset: {id,name,table_name,row_count,columns:[{name,type}],created_at}
POST /api/datasets {name:string,csv_text:string} -> Dataset (201). Browser file import reads
UTF-8 file locally and posts same JSON; maximum CSV text 1 MiB, 10,000 rows, 30 columns.
GET /api/examples -> [{id,question,dataset_id}] (seeded ecommerce dataset examples).
POST /api/runs {dataset_id,question} -> Run (202), runs in background.
GET /api/runs -> list of newest 30 Run objects.
GET /api/runs/{id} -> Run.
GET /api/runs/{id}/queries -> list of Query objects.
GET /api/runs/{id}/events -> list of Event objects.
GET /api/runs/{id}/report -> downloadable Markdown; 409 until complete.

Dataset names are labels. SQL table names are generated `d_<hex>` identifiers; columns are
validated simple ASCII identifiers. Initial dataset named `Demo Commerce` has columns
order_id TEXT, month TEXT, region TEXT, category TEXT, revenue REAL, cost REAL, orders INTEGER.

Run = {id,dataset_id,question,mode,status:'queued'|'running'|'completed'|'failed'|'interrupted',
created_at,error:null|string,report:null|Report,metrics:{model_calls,tool_calls,duration_ms}}
Report = {title:string,summary:string,claims:[Claim],chart:null|Chart,limitations:[string],verified:bool}
Claim = {label:string,query_id:string,row:integer,column:string,value:number|string|null,unit:string}
Chart = {query_id:string,label_column:string,value_column:string,title:string}
Query = {id,run_id,dataset_id,sql,columns:[string],rows:[[JSON scalar]],row_count,truncated:bool,
duration_ms,created_at}. Rows are zero-indexed; claims must equal exactly that query cell;
claims and charts may reference only this run's queries. Max 200 query rows.
Event = {id,run_id,type,message,data,created_at}. Types started/tool_call/tool_result/model_call/
verification/completed/failed. Events contain action summaries, not chain-of-thought.

Agent tools: inspect_schema(), profile_dataset(), query_sql(sql), submit_report(report fields).
The actual generated table name is given in tool results. SQL runs in a separate in-memory
SQLite database containing only the selected dataset; query_only, strict authorizer, read-only
SELECT prefix, SQL length/row/opcode/time limits, allowlisted SQL functions, no extension/I/O.
Tool outputs and issue text are untrusted data. Never execute generated Python or shell commands.

Demo routes a finite set of labeled questions to real SELECT queries and verified cell claims.
Unknown questions abstain visibly; imported datasets can be explored in live mode. UI must show
demo versus live clearly and never present synthetic eval as live-model accuracy.
