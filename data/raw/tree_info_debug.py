from trilogy import Dialects, Environment
from trilogy.dialect import DuckDBConfig
from trilogy.hooks import DebuggingHook
from pathlib import Path


DebuggingHook()

worker = Dialects.DUCK_DB.default_executor(environment= Environment(working_path = Path(__file__).parent),conf= DuckDBConfig(enable_python_datasources=True),
                                           )

worker.parse_file("tree_info_debug.preql")


sql = worker.generate_sql('''# --- Merged union datasource ---

select
    city, count(tree_id) as tree_count
limit 100;
''')