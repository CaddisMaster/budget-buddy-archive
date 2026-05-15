
import pandas as pd
from ingest import load_transactions

def clean_transactions(df):
    df = df.dropna(subset=['amount', 'transaction_date'])
    df['description'] = df['description'].str.strip()
    df['category'] = df['category'].str.strip()
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df['transaction_date'] = pd.to_datetime(df['transaction_date'], errors='coerce')
    df = df.dropna(subset=['amount', 'transaction_date'])
    print(f"Clean rows remaining: {len(df)}")
    print(df.head())
    return df

if __name__ == "__main__":
    df = load_transactions()
    clean_transactions(df)