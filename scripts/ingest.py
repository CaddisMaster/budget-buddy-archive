import pandas as pd
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'transactions.csv')

def load_transactions():
    df = pd.read_csv(DATA_PATH)
    print(f"Loaded {len(df)} rows")
    print(df.head())
    return df

if __name__ == "__main__":
    load_transactions()