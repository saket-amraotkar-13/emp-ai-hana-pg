from langchain_openai import ChatOpenAI
from langchain_community.agent_toolkits import create_sql_agent
from dotenv import load_dotenv
import os
from langchain_community.utilities.sql_database import SQLDatabase
from sqlalchemy import create_engine, text

load_dotenv()

os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGSMITH_API_KEY", "")
os.environ["LANGSMITH_ENDPOINT"] = os.getenv("LANGSMITH_ENDPOINT", "")
os.environ["LANGSMITH_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

db_user = os.getenv("db_user")
db_password = os.getenv("db_password")
db_host = os.getenv("db_host")
db_port = os.getenv("db_port")
db_name = os.getenv("db_name")


def connection_test(conn_str):
    try:
        engine = create_engine(
            conn_str,
            connect_args={"connect_timeout": 10}
        )

        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM DUMMY"))
            print("Connection successful!")

    except Exception as e:
        print(f"Connection failed: {e}")


connection_str = f"hana://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
connection_test(conn_str=connection_str)

db = SQLDatabase.from_uri(connection_str)

llm = ChatOpenAI(model="gpt-5.4", temperature=0)

agent_executor = create_sql_agent(
    llm=llm,
    db=db,
    verbose=False,
    agent_type="openai-tools"
)

while True:
    user_input = input("\nEnter your query for Employee Information: ")

    if user_input.lower() in ["exit", "quit"]:
        print("Exiting the application.")
        break

    try:
        response = agent_executor.invoke({"input": user_input})
        print(f"\nAI Response:\n{response['output']}")
    except Exception as e:
        print(f"Error while processing your query: {e}")