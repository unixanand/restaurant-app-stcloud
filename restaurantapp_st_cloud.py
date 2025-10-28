import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
from datetime import datetime, timedelta, date
import time
import pytz
import psycopg
import os
import re
from dotenv import load_dotenv
import logging
import io

from streamlit.web import cli as stcli
import sys

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Define BASE_DIR for consistent file paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FILES_DIR = os.environ.get('FILES_DIR', os.path.join(BASE_DIR, 'Files'))
BULK_DIR = os.environ.get('BULK_DIR', os.path.join(BASE_DIR, 'Bulk_Import'))
REPORTS_DIR = os.environ.get('REPORTS_DIR', os.path.join(BASE_DIR, 'reports'))
os.makedirs(FILES_DIR, exist_ok=True)
os.makedirs(BULK_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

load_dotenv()  # Load environment variables from .env file

SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
EMAIL_USER = os.environ.get('EMAIL_USER')
EMAIL_PASS = os.environ.get('EMAIL_PASS')
ALERT_RECIPIENT = os.environ.get('ALERT_RECIPIENT', EMAIL_USER)  # Default to sender
SEND_ALERTS = os.environ.get('SEND_ALERTS', 'true').lower() == 'true'

@st.cache_resource
def get_connection():
    """Load DB credentials from environment variables and connect to PostgreSQL."""
    host = os.environ.get('DB_HOST')
    port = os.environ.get('DB_PORT', '6543')
    dbname = os.environ.get('DB_NAME')
    user = os.environ.get('DBP_USER')
    password = os.environ.get('DBP_PASSWORD')
    print("host=", host)
    if not all([host, dbname, user, password]):
        st.error("Missing DB environment variables: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD")
        return None
    try:
        connection = psycopg.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            sslmode='require', # For Supabase/SSL-enabled PG
            prepare_threshold=None 
        )
        st.success("Connected to PostgreSQL Database!")
        return connection
    except Exception as e:
        st.write(f"DB Connection Error: {e}")
        return None


# --- Inlined Functions from Original App (Adapted for Streamlit) ---

def load_stock_txn_data(connection) :
    rec_cnt = 0
    
    qry = "SELECT COUNT(*) cnt FROM STOCK_MAINTENANCE_TXN_TBL WHERE value_date = CURRENT_DATE"
    ins_qry = "INSERT INTO STOCK_MAINTENANCE_TXN_TBL (value_date, item_name, avail_stock) SELECT CURRENT_DATE, item_name, total_stock FROM STOCK_MAINTENANCE_TBL WHERE delete_flag='N' "
    
    cursor = connection.cursor()
    cursor.execute(qry)
    row = cursor.fetchone()
    rec_cnt = row[0] if row else 0

    if rec_cnt == 0 :
        cursor.execute(ins_qry)
        connection.commit()

def load_tax_data(connection):
    """Load tax categories and rates."""
    cursor = connection.cursor()
    sel_tax_rec = "SELECT category_name, tax_slab FROM tax_maintenance_tbl"
    cursor.execute(sel_tax_rec)
    tax_data = {}
    rows = cursor.fetchall()
    for row in rows:
        tax_data[row[0]] = row[1]
    cursor.close()
    return tax_data

def get_stock_data(connection):
    """Load stock for current date."""
    cursor = connection.cursor()
    sel_qry = "SELECT item_name, avail_stock FROM STOCK_MAINTENANCE_TXN_TBL WHERE value_date = CURRENT_DATE"
    cursor.execute(sel_qry)
    stock_rec = {}
    rows = cursor.fetchall()
    for rec in rows:
        stock_rec[rec[0]] = int(rec[1])
    cursor.close()
    return stock_rec

def get_shortage_stock_data(connection):
    """Load shortage stock for current date."""
    cursor = connection.cursor()
    sel_qry = "SELECT item_name, avail_stock FROM STOCK_MAINTENANCE_TXN_TBL WHERE value_date = CURRENT_DATE AND avail_stock = 0"
    cursor.execute(sel_qry)
    stock_rec = {}
    rows = cursor.fetchall()
    for rec in rows:
        stock_rec[rec[0]] = int(rec[1])
    cursor.close()
    return stock_rec

def load_shortage_stock_data(connection):
    """Load shortage stock for current date."""
    cursor = connection.cursor()
    upd_qry = "UPDATE STOCK_MAINTENANCE_TXN_TBL SET avail_stock=50 WHERE value_date = CURRENT_DATE AND avail_stock = 0"
    cursor.execute(upd_qry)
    connection.commit()
    cursor.close()

def update_stock_rec(connection, stock_rec):
    """Update stock in DB."""
    cursor = connection.cursor()
    upd_qry = "UPDATE STOCK_MAINTENANCE_TXN_TBL SET avail_stock = %(qty)s WHERE value_date = CURRENT_DATE AND item_name = %(itm)s"
    for itm, qty in stock_rec.items():
        cursor.execute(upd_qry, {"qty": qty, "itm": itm})
    connection.commit()
    cursor.close()

def update_tax_amt(connection,tax_category,tax_amount) :
    category = str(tax_category)
    amt = float(tax_amount)
    cursor = connection.cursor()
    
    upd_qry = "UPDATE TAX_MAINTENANCE_TBL SET tax_slab = %(amt)s WHERE category_name = %(category)s"
    try:
        cursor.execute(upd_qry,{"amt" : amt, "category" : category})
        connection.commit()
    except psycopg.Error as e:
        st.error(f"DB Update Error: {e}")
    cursor.close()

def insert_db_data(connection, tmp_lis):
    """Insert sales to DB."""
    cursor = connection.cursor()
    current_date = date.today().strftime("%d-%b-%Y").upper()
    ins_rec = []
    for idx in range(len(tmp_lis)):
        ins_rec.append([current_date, str(tmp_lis[idx][0]), str(tmp_lis[idx][1]), str(tmp_lis[idx][2])])
    insert_sales_rec = "INSERT INTO sales_dtl_tbl (value_date, item_name, quantity, sales_amt) VALUES (%s, %s, %s, %s)"
    try:
        cursor.executemany(insert_sales_rec, ins_rec)
        connection.commit()
    except psycopg.Error as e:
        st.error(f"DB Insert Error: {e}")
    cursor.close()

def fetch_coffee_df(connection):
    """Fetch available coffee menu."""
    cursor = connection.cursor()
    sel_qry1 = "SELECT ROW_NUMBER() OVER () rn, coffee_name, price, tax_category FROM coffee_menu_tbl a WHERE a.coffee_name IN (SELECT b.item_name FROM STOCK_MAINTENANCE_TXN_TBL b "
    sel_qry2 = "WHERE a.coffee_name=b.item_name AND value_date = CURRENT_DATE AND avail_stock > 0) AND delete_flag='N'"
    cursor.execute(sel_qry1 + sel_qry2)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['ItemNo', 'Name', 'Price', 'TaxCategory'])
    cursor.close()
    return df

def fetch_tea_df(connection):
    """Fetch available tea menu."""
    cursor = connection.cursor()
    sel_qry1 = "SELECT ROW_NUMBER() OVER () rn, tea_name, price, tax_category FROM tea_menu_tbl a WHERE a.tea_name IN (SELECT b.item_name FROM STOCK_MAINTENANCE_TXN_TBL b "
    sel_qry2 = "WHERE a.tea_name=b.item_name AND value_date = CURRENT_DATE AND avail_stock > 0) AND delete_flag='N'"
    cursor.execute(sel_qry1 + sel_qry2)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['ItemNo', 'Name', 'Price', 'TaxCategory'])
    cursor.close()
    return df

def fetch_chat_df(connection, category):
    """Fetch chat menu (Veg/Non-Veg/Both)."""
    cursor = connection.cursor()
    if category == "VEG":
        select_rec = "SELECT ROW_NUMBER() OVER () rn, chat_name, price, tax_category FROM chat_menu_tbl a WHERE category = 'VEG' AND a.chat_name IN (SELECT b.item_name FROM STOCK_MAINTENANCE_TXN_TBL b WHERE a.chat_name = b.item_name AND value_date = CURRENT_DATE AND avail_stock > 0) AND a.delete_flag='N'"
    elif category == "NV":
        select_rec = "SELECT ROW_NUMBER() OVER () rn, chat_name, price, tax_category FROM chat_menu_tbl a WHERE category = 'NV' AND a.chat_name IN (SELECT b.item_name FROM STOCK_MAINTENANCE_TXN_TBL b WHERE a.chat_name = b.item_name AND value_date = CURRENT_DATE AND avail_stock > 0) AND a.delete_flag='N'"
    else : 
        select_rec = "SELECT ROW_NUMBER() OVER () rn, chat_name, price, tax_category FROM chat_menu_tbl a WHERE a.chat_name IN (SELECT b.item_name FROM STOCK_MAINTENANCE_TXN_TBL b WHERE a.chat_name = b.item_name AND value_date = CURRENT_DATE AND avail_stock > 0) AND a.delete_flag='N'"
    cursor.execute(select_rec)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['ItemNo', 'Name', 'Price', 'TaxCategory'])
    cursor.close()
    return df

def check_time():
    """Check if current time is within special menu hours (17:00-19:00)."""
    local_time = datetime.now(pytz.timezone("Asia/Kolkata"))
    HH = int(local_time.strftime("%H"))
    return 1 if 17 <= HH <= 19 else 0

def fetch_spl_df(connection):
    """Fetch special snacks menu if within time."""
    
    if check_time() == 0:
        return pd.DataFrame()
    cursor = connection.cursor()
    sel_qry = "SELECT ROW_NUMBER() OVER () rn, item_name, price, tax_category FROM special_snacks_tbl WHERE delete_flag='N'"
    cursor.execute(sel_qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['ItemNo', 'Name', 'Price', 'TaxCategory'])
    cursor.close()
    return df

def fetch_snack_df(connection):
    """Fetch special snacks menu for maintenance"""
    cursor = connection.cursor()
    sel_qry = "SELECT ROW_NUMBER() OVER () rn, item_name, price, tax_category FROM special_snacks_tbl WHERE delete_flag='N'"
    cursor.execute(sel_qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['ItemNo', 'Name', 'Price', 'TaxCategory'])
    cursor.close()
    return df

def coffee_sales_fig(connection, period):
    """Generate coffee sales chart."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE item_name IN (SELECT coffee_name FROM coffee_menu_tbl) AND value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' AND item_name IN (SELECT coffee_name FROM coffee_menu_tbl) GROUP BY item_name"
    else:  # monthly
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' AND item_name IN (SELECT coffee_name FROM coffee_menu_tbl) GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce')
    if not df.empty:
        fig, ax = plt.subplots()
        df.plot(kind='bar', x='Item', y='Quantity', ax=ax, color=plt.cm.Set3(np.linspace(0, 1, len(df))),title=f'Coffee Sales ({period.capitalize()})')
        ax.set_xlabel('Coffee Flavor')
        ax.set_ylabel('Sales Quantity')
        return fig
    return None

def coffee_sales_data(connection, period):
    """Generate coffee sales chart."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE item_name IN (SELECT coffee_name FROM coffee_menu_tbl) AND value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' AND item_name IN (SELECT coffee_name FROM coffee_menu_tbl) GROUP BY item_name"
    else:  # monthly
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' AND item_name IN (SELECT coffee_name FROM coffee_menu_tbl) GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    if not df.empty:
        return df
    return None

def tea_sales_fig(connection, period='daily'):
    """Generate tea sales chart (similar to coffee)."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE item_name IN (SELECT tea_name FROM tea_menu_tbl) AND value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' AND item_name IN (SELECT tea_name FROM tea_menu_tbl) GROUP BY item_name"
    else:
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' AND item_name IN (SELECT tea_name FROM tea_menu_tbl) GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce')
    if not df.empty:
        fig, ax = plt.subplots()
        df.plot(kind='bar', x='Item', y='Quantity', ax=ax,color=plt.cm.Set3(np.linspace(0, 1, len(df))), title=f'Tea Sales ({period.capitalize()})')
        ax.set_xlabel('Tea Type')
        ax.set_ylabel('Sales Quantity')
        return fig
    return None

def tea_sales_data(connection, period='daily'):
    """Generate tea sales chart (similar to coffee)."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE item_name IN (SELECT tea_name FROM tea_menu_tbl) AND value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' AND item_name IN (SELECT tea_name FROM tea_menu_tbl) GROUP BY item_name"
    else:
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' AND item_name IN (SELECT tea_name FROM tea_menu_tbl) GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    if not df.empty:
        return df
    return None

def chat_sales_fig(connection, period='daily'):
    """Generate chat sales chart (similar to coffee)."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE item_name IN (SELECT chat_name FROM chat_menu_tbl) AND value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' AND item_name IN (SELECT chat_name FROM chat_menu_tbl) GROUP BY item_name"
    else:
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' AND item_name IN (SELECT chat_name FROM chat_menu_tbl) GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce')
    if not df.empty:
        fig, ax = plt.subplots()
        df.plot(kind='bar', x='Item', y='Quantity', ax=ax, color=plt.cm.Set3(np.linspace(0, 1, len(df))), title=f'Chat Sales ({period.capitalize()})')
        ax.set_xlabel('Chat Type')
        ax.set_ylabel('Sales Quantity')
        return fig
    return None

def chat_sales_data(connection, period='daily'):
    """Generate chat sales chart (similar to coffee)."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE item_name IN (SELECT chat_name FROM chat_menu_tbl) AND value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' AND item_name IN (SELECT chat_name FROM chat_menu_tbl) GROUP BY item_name"
    else:
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' AND item_name IN (SELECT chat_name FROM chat_menu_tbl) GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    if not df.empty:
        return df
    return None

def Spl_sales_fig(connection, period='daily'):
    """Generate snacks sales chart (similar to coffee)."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE item_name IN (SELECT item_name FROM special_snacks_tbl) AND value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' AND item_name IN (SELECT item_name FROM special_snacks_tbl) GROUP BY item_name"
    else:
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' AND item_name IN (SELECT item_name FROM special_snacks_tbl) GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce')
    if not df.empty:
        fig, ax = plt.subplots()
        df.plot(kind='bar', x='Item', y='Quantity', ax=ax, color=plt.cm.Set3(np.linspace(0, 1, len(df))), title=f'Snacks Sales ({period.capitalize()})')
        ax.set_xlabel('Snack Type')
        ax.set_ylabel('Sales Quantity')
        return fig
    return None

def Spl_sales_data(connection, period='daily'):
    """Generate snacks sales chart (similar to coffee)."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE item_name IN (SELECT item_name FROM special_snacks_tbl) AND value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' AND item_name IN (SELECT item_name FROM special_snacks_tbl) GROUP BY item_name"
    else:
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' AND item_name IN (SELECT item_name FROM special_snacks_tbl) GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    if not df.empty:
        return df
    return None

def pull_week_data(connection) :
    current_date = datetime.today()
    start_of_week = current_date - timedelta(days=current_date.weekday())
    start_date = start_of_week.date()

    week_start_date = start_date.strftime("%d-%b-%Y").upper()

    sel_qry1 = "SELECT SUBSTRING(REPLACE(TO_CHAR(value_date, 'DD Mon'), ' ', '-'), 1, 6) AS day, item_name, quantity, SUM(sales_amt) tot_sales FROM sales_dtl_tbl "
    sel_qry2 = "WHERE value_date >= %s GROUP BY value_date, item_name, quantity ORDER BY 1,2"
    final_qry = sel_qry1 + sel_qry2

    cursor = connection.cursor()
    cursor.execute(final_qry, (week_start_date,))
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns =['Day','Item','Quantity','Tot.Sales'])
    cursor.close()
    return df

def execute_qry(connection, qry_str,column_names) :
    cursor = connection.cursor()
    cursor.execute(qry_str)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns = column_names)
    cursor.close()
    return df

def pull_month_data(connection):
    path = os.path.join(FILES_DIR, "pg_week_wise_sales.txt")
    with open(path, "r") as fp:
        qry = fp.read()
    cursor = connection.cursor()
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['WeekNo', 'Category', 'Item', 'Tot.Quantity', 'Tot.Sales'])
    cursor.close()
    return df

def get_month_data(connection) :
    item_lis = []
    path = os.path.join(BASE_DIR, "Files", "pg_week_wise_sales.txt")
    fp = open(path,"r")
    cursor = connection.cursor()
    qry = fp.read()
    fp.close()
    cursor.execute(qry)
    rows = cursor.fetchall()
    for row in rows:
        item_lis.append(row)
    cursor.close()
    return item_lis

def overall_sales_fig(connection, period='daily'):
    """Generate overall sales chart (similar to coffee)."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' GROUP BY item_name"
    else:
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce')
    if not df.empty:
        fig, ax = plt.subplots()
        df.plot(kind='bar', x='Item', y='Quantity', ax=ax,color=plt.cm.Set3(np.linspace(0, 1, len(df))), title=f'OverAll Sales ({period.capitalize()})')
        ax.set_xlabel('Item Type')
        ax.set_ylabel('Sales Quantity')
        return fig
    return None

def overall_sales_data(connection, period='daily'):
    """Generate overall sales chart (similar to coffee)."""
    cursor = connection.cursor()
    if period == 'Daily':
        qry = "SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date = CURRENT_DATE GROUP BY item_name"
    elif period == 'Weekly':
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%d-%b-%Y").upper()
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE value_date >= '{week_start}' GROUP BY item_name"
    else:
        year_month = datetime.now().strftime("%Y-%m")
        qry = f"SELECT item_name, SUM(quantity) as qty FROM sales_dtl_tbl WHERE TO_CHAR(value_date, 'YYYY-MM') = '{year_month}' GROUP BY item_name"
    cursor.execute(qry)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=['Item', 'Quantity'])
    cursor.close()
    if not df.empty:
        return df
    return None

def Week_sale_items(connection) :
    item_lis = []
    
    current_date = datetime.today()
    start_of_week = current_date - timedelta(days=current_date.weekday())
    start_date = start_of_week.date()

    week_start_date = start_date.strftime("%d-%b-%Y").upper()
    
    cursor = connection.cursor()

    sel_qry1 = "SELECT SUBSTRING(REPLACE(TO_CHAR(value_date, 'DD Mon'), ' ', '-'), 1, 6) AS day, 'Coffee' item_name, SUM(sales_amt) tot_sales FROM sales_dtl_tbl "
    sel_qry2 = "WHERE value_date >= %s AND item_name IN (SELECT coffee_name FROM coffee_menu_tbl) GROUP BY value_date, item_name "
    sel_qry3 = "UNION SELECT SUBSTRING(REPLACE(TO_CHAR(value_date, 'DD Mon'), ' ', '-'), 1, 6) AS day, 'Tea' item_name, SUM(sales_amt) tot_sales FROM sales_dtl_tbl "
    sel_qry4 = "WHERE value_date >= %s AND item_name IN (SELECT tea_name FROM tea_menu_tbl) GROUP BY value_date, item_name "
    sel_qry5 = "UNION SELECT SUBSTRING(REPLACE(TO_CHAR(value_date, 'DD Mon'), ' ', '-'), 1, 6) AS day, 'Chat' item_name, SUM(sales_amt) tot_sales FROM sales_dtl_tbl "
    sel_qry6 = "WHERE value_date >= %s AND item_name IN (SELECT chat_name FROM chat_menu_tbl) GROUP BY value_date, item_name "
    sel_qry7 = "UNION SELECT SUBSTRING(REPLACE(TO_CHAR(value_date, 'DD Mon'), ' ', '-'), 1, 6) AS day, 'Snacks' item_name, SUM(sales_amt) tot_sales FROM sales_dtl_tbl "
    sel_qry8 = "WHERE value_date >= %s AND item_name IN (SELECT item_name FROM special_snacks_tbl) GROUP BY value_date, item_name"
    
    final_qry = sel_qry1 + sel_qry2 + sel_qry3 + sel_qry4 + sel_qry5 + sel_qry6 + sel_qry7 + sel_qry8

    cursor.execute(final_qry, (week_start_date, week_start_date, week_start_date, week_start_date))

    rows = cursor.fetchall()
    for row in rows:
        rec = tuple(row)
        item_lis.append(rec)

    cursor.close()
    return item_lis

def validate_item(connection, item):
    cursor = connection.cursor()
    sel_qry = "SELECT 1 FROM BULK_ORDER_TBL WHERE item_name = %(itm)s"
    try:
        cursor.execute(sel_qry, {"itm": item})
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            logging.warning(f"Invalid item: {item}")
            return 0
        logging.info(f"Validated item: {item}")
        return 1
    except psycopg.Error as e:
        logging.error(f"DB Fetch Error for item {item}: {e}")
        st.error(f"DB Fetch Error: {e}")
        return 0

def get_item_stock(connection, item,qty) :
    chk = check_time()
    
    cursor = connection.cursor()
    chk_qry = "SELECT spl_flag FROM BULK_ORDER_TBL WHERE item_name = %(item)s"
    cursor.execute(chk_qry,{"item" : item})
    row = cursor.fetchone()
    chk_flg = row[0] if row else None
    if chk_flg == 'Y' and chk == 0 :
        avail_stock = 0
        qty = 0
        cursor.close()
        return avail_stock, qty
                   
    stk_qry = "SELECT avail_stock FROM STOCK_MAINTENANCE_TXN_TBL WHERE item_name = %(item)s AND value_date = CURRENT_DATE"
    cursor.execute(stk_qry, {"item" : item})
    row = cursor.fetchone()
    avail_stock = row[0] if row else 0
    cursor.close()
    
    if avail_stock == 0 :
        qty = 0
        return avail_stock, qty
    
    elif avail_stock >= qty :
        avail_stock -= qty
        
        cursor = connection.cursor()
        upd_qry = "UPDATE STOCK_MAINTENANCE_TXN_TBL SET avail_stock = %(avail_stock)s WHERE item_name = %(item)s AND value_date = CURRENT_DATE"
        try:
            cursor.execute(upd_qry, {"item" : item, "avail_stock" : avail_stock})
        except psycopg.Error as e:
            st.error(f"DB Update Error: {e}")
        connection.commit()
        cursor.close()
        return avail_stock, qty
        
    else :
        qty = avail_stock
        avail_stock = 0
        cursor = connection.cursor()
        try:
            upd_qry = "UPDATE STOCK_MAINTENANCE_TXN_TBL SET avail_stock = 0 WHERE item_name = %(item)s AND value_date = CURRENT_DATE"
            cursor.execute(upd_qry, {"item" : item})
        except psycopg.Error as e:
            st.error(f"DB Update Error: {e}")
        connection.commit()
        cursor.close() 
        return avail_stock, qty

logging.basicConfig(level=logging.INFO, filename=os.path.join(BASE_DIR, 'Bulk_Import', 'bulk_order.log'))

def insert_log(connection, file, message):
    if not file:
        logging.info("No file provided for logging")
        return
    cursor = connection.cursor()
    sel_qry = "SELECT 1 FROM bulk_order_log_tbl WHERE to_date(value_date,'YYYY-MM-DD') = CURRENT_DATE AND log_message = %(msg)s AND file_name = %(fil)s"
    cursor.execute(sel_qry, {"msg": message, "fil": file})
    row = cursor.fetchone()
    cursor.close()
    if row is None:
        cursor = connection.cursor()
        ins_qry = "INSERT INTO bulk_order_log_tbl(value_date, file_name, log_message) VALUES(CURRENT_DATE, %(2)s, %(3)s)"
        try:
            cursor.execute(ins_qry, {"2": file, "3": message})
            logging.info(f"Logged: {message} for file {file}")
        except psycopg.Error as e:
            logging.error(f"DB Insert Error: {e}")
            return
        connection.commit()
        cursor.close()

def load_bulk_header(connection,file,status) :
    if len(file) == 0 :
        return
    cursor = connection.cursor()
    sel_qry = "SELECT 1 FROM bulk_order_header_tbl WHERE value_date = CURRENT_DATE AND file_name = %(fil)s"
    cursor.execute(sel_qry, {"fil" : file})
    row = cursor.fetchone()
    cursor.close()
    if row is None :
        cursor = connection.cursor()
        ins_qry = "INSERT INTO bulk_order_header_tbl VALUES(CURRENT_DATE, %(fil)s, %(stat)s)"
        cursor.execute(ins_qry, {"fil" : file, "stat" : status})
        connection.commit()
        cursor.close()

def update_bulk_header(connection,file,status) :
    cursor = connection.cursor()
    upd_qry = "UPDATE bulk_order_header_tbl SET status = %(st)s WHERE value_date = CURRENT_DATE AND file_name = %(fil)s"
    cursor.execute(upd_qry, {"fil" : file, "st" : status})
    connection.commit()
    cursor.close()

def check_bulk_header(connection,file) :
    if len(file) != 0 :
        cursor = connection.cursor()
        chk_qry = "SELECT 1 FROM bulk_order_header_tbl WHERE value_date = CURRENT_DATE AND file_name = %(fil)s AND status='Processed' "
        cursor.execute(chk_qry, {"fil" : file})
        row = cursor.fetchone()
        cursor.close()
        if row is None :
            return 0
        return 1

def get_item_price(connection,item,qty) :
    cursor = connection.cursor()
    sel_qry = "SELECT price, tax_category FROM BULK_ORDER_TBL WHERE item_name = %(itm)s"
    try :
        cursor.execute(sel_qry,{"itm" :item})
    except psycopg.Error as e:
        st.error(f"DB Fetch Error: {e}")
    row = cursor.fetchone()
    price = 0
    tax_cat = None
    if row:
        if int(row[0]) > 0 :
            price = row[0] * qty
            tax_cat = row[1]
    cursor.close()

    if tax_cat:
        tax_qry = "SELECT tax_slab FROM TAX_MAINTENANCE_TBL WHERE category_name = %(tax_cat)s"
        cursor = connection.cursor()
        cursor.execute(tax_qry, {"tax_cat" : tax_cat})
        row = cursor.fetchone()
        tax = row[0] if row else 0
        cursor.close()
        tax_amt = price * tax
    else:
        tax_amt = 0

    return price, tax_amt

##
def send_stock_alert(connection, item_name, new_stock):
    """Send email alert if stock is low/zero."""
    if new_stock > 0:  # Customize threshold here (e.g., > 5)
        return  # No alert needed
    
    # Check for recent alert (simple DB flag to avoid spam; add column if needed)
    cursor = connection.cursor()
    cursor.execute("SELECT 1 FROM stock_alerts WHERE item_name = %s AND alert_date = CURRENT_DATE", (item_name,))
    if cursor.fetchone():
        cursor.close()
        return  # Already alerted today
    cursor.close()
    
    # Email setup
    msg = MIMEMultipart('alternative')
    msg['From'] = EMAIL_USER
    msg['To'] = ALERT_RECIPIENT
    msg['Subject'] = f"🚨 Stock Shortage Alert: {item_name} Out of Stock!"
    
    html_body = f"""
    <html>
    <body>
        <h2>Stock Alert for {item_name}</h2>
        <p>Current stock level: <strong>{new_stock}</strong></p>
        <p>Date: {datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")}</p>
        <p>Action needed: Replenish immediately to avoid order issues.</p>
        <p>Dashboard: <a href="https://restaurant-app-anand.streamlit.app/">View Now</a></p>
    </body>
    </html>
    """
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        if SEND_ALERTS:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            text = server.send_message(msg)
            server.quit()
            st.success(f"Alert sent for {item_name}!")
        else:
            st.info(f"Demo: Would send alert for {item_name} (stock: {new_stock})")
        
        # Log to DB (create table if needed: CREATE TABLE stock_alerts (item_name VARCHAR, alert_date DATE);)
        cursor = connection.cursor()
        cursor.execute("INSERT INTO stock_alerts (item_name, alert_date) VALUES (%s, CURRENT_DATE)", (item_name,))
        connection.commit()
        cursor.close()
    except Exception as e:
        st.error(f"Email alert failed: {e}")
        logging.error(f"Stock alert error for {item_name}: {e}")
##
def send_sms_alert(item_name, new_stock):
    """Send SMS via email gateway (free)."""
    if not os.environ.get('SMS_GATEWAYS', 'false').lower() == 'true':
        return
    
    sms_emails = [phone.strip() for phone in os.environ.get('SMS_RECIPIENTS', '').split(',')] if os.environ.get('SMS_RECIPIENTS') else []
    
    if not sms_emails:
        st.warning("[DEBUG] No SMS recipients configured.")
        return
    
    # Reuse email setup (short text version)
    message = "Test msg"
    msg = MIMEText(message, 'plain')
    #msg = MIMEText(f"🚨 Stock Alert: {item_name} out of stock! Level: {new_stock}. Replenish now. {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M')}", 'plain')
    msg['Subject'] = ''  # No subject for SMS
    msg['From'] = EMAIL_USER
    
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        for sms_email in sms_emails:  # Send to each
            msg['To'] = sms_email
            server.send_message(msg)
        server.quit()
        st.success(f"SMS alert sent for {item_name} to {len(sms_emails)} numbers!")
        st.write(f"[DEBUG] SMS sent to {sms_emails}")
    except Exception as e:
        st.error(f"SMS alert failed: {e}")
        logging.error(f"SMS error for {item_name}: {e}")
        
# --- Initialize Session State ---

if 'initialized' not in st.session_state:
    st.session_state.order_menu = {}
    st.session_state.stock_rec = {}
    st.session_state.tax_data = {}
    st.session_state.tax_lis = {}
    st.session_state.bulk_lis = []
    st.session_state.initialized = True

if 'order_menu' not in st.session_state:
    st.session_state.order_menu = {}
    st.session_state.stock_rec = {}
    st.session_state.tax_data = {}
    st.session_state.tax_lis = {}  # For item-specific tax categories

# --- Main App ---
st.set_page_config(page_title="Restaurant Dashboard", layout="wide", initial_sidebar_state="expanded")
st.title("🍽️ Interactive Restaurant Management Dashboard")

connection = get_connection()
if not connection:
    st.stop()

# Insert stock txn data
load_stock_txn_data(connection)
# Load tax and stock on startup
try:
    st.session_state.tax_data = load_tax_data(connection)
    st.session_state.stock_rec = get_stock_data(connection)
    st.success("Data loaded! Select a portal in the sidebar.")
except Exception as e:
    st.error(f"Error loading tax/stock data: {e}")
st.markdown("""
<style>
.stButton>button {
    background-color: #8b4513;
    color: white;
    border-radius: 10px;
}
</style>
""", unsafe_allow_html=True)
# Sidebar for Portal Selection
portal = st.sidebar.selectbox("Select Portal", ["Public (Order)","Corporate (Admin)"])
if st.sidebar.button("Logout"):
    st.session_state.clear()
    st.rerun()

# --- Public Portal ---
if portal == "Public (Order)":
    st.header("🛒 Public Portal: Place Orders")
    tab1, tab2, tab3, tab4, tab_cart, tab_bill = st.tabs(["Coffee", "Tea", "Chat", "Special", "Cart", "Bill"])
    
    with tab1:  # Coffee
        st.subheader("☕ Coffee Menu")
        df_coffee = fetch_coffee_df(connection)

        if not df_coffee.empty:
            st.dataframe(df_coffee[['ItemNo', 'Name', 'Price']])
            
            item_options = df_coffee.set_index('ItemNo')['Name'].to_dict()
            selected_item_no = st.selectbox("Choose Item", options=list(item_options.keys()), format_func=lambda x: f"{x}: {item_options[x]}")
            item_name = item_options[selected_item_no]
            max_stock = st.session_state.stock_rec.get(item_name, 0)
            quantity = st.number_input(f"Quantity (Max: {max_stock})", min_value=0, max_value=max_stock, value=0,key="coffee_qty")
            
            col1, col2 = st.columns(2)
            with col1:
                submitted = st.button("Add Coffee Order")

                if submitted:
                    if quantity > 0:
                        price = df_coffee[df_coffee['ItemNo'] == selected_item_no]['Price'].values[0]
                        tax_cat = df_coffee[df_coffee['ItemNo'] == selected_item_no]['TaxCategory'].values[0]
                        idx = len(st.session_state.order_menu)
                        st.session_state.order_menu[idx] = [item_name, quantity, price]
                        st.session_state.tax_lis[item_name] = tax_cat
                        st.session_state.stock_rec[item_name] -= quantity
                        update_stock_rec(connection, st.session_state.stock_rec)
                        st.success(f"Added {quantity} x {item_name}!")
                        if st.session_state.stock_rec[item_name] <= 0:
                            send_stock_alert(connection, item_name, st.session_state.stock_rec[item_name])
                            #send_sms_alert(item_name, st.session_state.stock_rec[item_name])
                        st.rerun()
                    else:
                        st.error("Please select a quantity greater than 0.")

        else:
            st.warning("No coffee items available.")

    with tab2:  # Tea
        st.subheader("🫖 Tea Menu")
        df_tea = fetch_tea_df(connection)
        if not df_tea.empty:
            st.dataframe(df_tea[['ItemNo', 'Name', 'Price']])
            
            item_options = df_tea.set_index('ItemNo')['Name'].to_dict()
            selected_item_no = st.selectbox("Choose Item", options=list(item_options.keys()), format_func=lambda x: f"{x}: {item_options[x]}")
            item_name = item_options[selected_item_no]
            max_stock = st.session_state.stock_rec.get(item_name, 0)
            quantity = st.number_input(f"Quantity (Max: {max_stock})", min_value=0, max_value=max_stock, value=0,key="tea_qty")
            submitted = st.button("Add Tea Order")
            if submitted and quantity > 0:
                price = df_tea[df_tea['ItemNo'] == selected_item_no]['Price'].values[0]
                tax_cat = df_tea[df_tea['ItemNo'] == selected_item_no]['TaxCategory'].values[0]
                idx = len(st.session_state.order_menu)
                st.session_state.order_menu[idx] = [item_name, quantity, price]
                st.session_state.tax_lis[item_name] = tax_cat
                st.session_state.stock_rec[item_name] -= quantity
                update_stock_rec(connection, st.session_state.stock_rec)
                st.success(f"Added {quantity} x {item_name}!")
                if st.session_state.stock_rec[item_name] <= 0:
                    send_stock_alert(connection, item_name, st.session_state.stock_rec[item_name])
                    #send_sms_alert(item_name, st.session_state.stock_rec[item_name])
                st.rerun()
        else:
            st.warning("No tea items available.")

    with tab3:  # Chat
        st.subheader("🍗🥕 Chat Menu")
        category = st.selectbox("Category", ["Both", "VEG", "NV"])
        df_chat = fetch_chat_df(connection, category)
        if not df_chat.empty:
            st.dataframe(df_chat[['ItemNo', 'Name', 'Price']])
            
            item_options = df_chat.set_index('ItemNo')['Name'].to_dict()
            selected_item_no = st.selectbox("Choose Item", options=list(item_options.keys()), format_func=lambda x: f"{x}: {item_options[x]}")
            item_name = item_options[selected_item_no]
            max_stock = st.session_state.stock_rec.get(item_name, 0)
            quantity = st.number_input(f"Quantity (Max: {max_stock})", min_value=0, max_value=max_stock, value=0,key="chat_qty")
            submitted = st.button("Add Chat Order")
            if submitted and quantity > 0:
                price = df_chat[df_chat['ItemNo'] == selected_item_no]['Price'].values[0]
                tax_cat = df_chat[df_chat['ItemNo'] == selected_item_no]['TaxCategory'].values[0]
                idx = len(st.session_state.order_menu)
                st.session_state.order_menu[idx] = [item_name, quantity, price]
                st.session_state.tax_lis[item_name] = tax_cat
                st.session_state.stock_rec[item_name] -= quantity
                update_stock_rec(connection, st.session_state.stock_rec)
                st.success(f"Added {quantity} x {item_name}!")
                if st.session_state.stock_rec[item_name] <= 0:
                    send_stock_alert(connection, item_name, st.session_state.stock_rec[item_name])
                    #send_sms_alert(item_name, st.session_state.stock_rec[item_name])
                st.rerun()
        else:
            st.warning(f"No chat items available for {category}.")

    with tab4:  # Special
        st.subheader("🥂 Special Menu")
        
        df_spl = fetch_spl_df(connection)
        if not df_spl.empty:
            st.dataframe(df_spl[['ItemNo', 'Name', 'Price']])
            
            item_options = df_spl.set_index('ItemNo')['Name'].to_dict()
            selected_item_no = st.selectbox("Choose Item", options=list(item_options.keys()), format_func=lambda x: f"{x}: {item_options[x]}")
            item_name = item_options[selected_item_no]
            max_stock = st.session_state.stock_rec.get(item_name, 0)
            quantity = st.number_input(f"Quantity (Max: {max_stock})", min_value=0, max_value=max_stock, value=0,key="snack_qty")
            submitted = st.button("Add Snack Order")
            if submitted and quantity > 0:
                price = df_spl[df_spl['ItemNo'] == selected_item_no]['Price'].values[0]
                tax_cat = df_spl[df_spl['ItemNo'] == selected_item_no]['TaxCategory'].values[0]
                idx = len(st.session_state.order_menu)
                st.session_state.order_menu[idx] = [item_name, quantity, price]
                st.session_state.tax_lis[item_name] = tax_cat
                st.session_state.stock_rec[item_name] -= quantity
                update_stock_rec(connection, st.session_state.stock_rec)
                st.success(f"Added {quantity} x {item_name}!")
                if st.session_state.stock_rec[item_name] <= 0:
                    send_stock_alert(connection, item_name, st.session_state.stock_rec[item_name])
                    #send_sms_alert(item_name, st.session_state.stock_rec[item_name])
                st.rerun()
        else:
            st.warning("Special menu unavailable (only 5-7 PM).")

    with tab_cart:
        st.subheader("🛍️ Your Cart")
        if st.session_state.order_menu:
            order_df = pd.DataFrame.from_dict(st.session_state.order_menu, orient='index', columns=['Item', 'Qty', 'Unit Price'])
            order_df['Total'] = order_df['Qty'].astype(int) * order_df['Unit Price']
            st.dataframe(order_df)
            col1, col2, col3 = st.columns(3)
            with col1:
                cancel_idx = st.selectbox("Cancel Item #", options=list(st.session_state.order_menu.keys()))
                item_name = order_df.loc[cancel_idx, 'Item']
            with col2:
                st.text_input("Cancel Item",item_name)
            with col3:
                cancel_qty = st.number_input("Cancel Qty (partial for full)", min_value=1, max_value=order_df.loc[cancel_idx, 'Qty'])
            if st.button("Cancel"):
                item_name = order_df.loc[cancel_idx, 'Item']
                #if cancel_qty != 0:
                    #cancel_qty = order_df.loc[cancel_idx, 'Qty']
                    
                st.session_state.stock_rec[item_name] += cancel_qty
                if cancel_qty == order_df.loc[cancel_idx, 'Qty']:
                    del st.session_state.order_menu[cancel_idx]
                else:
                    st.session_state.order_menu[cancel_idx][1] -= cancel_qty
                update_stock_rec(connection, st.session_state.stock_rec)
                st.success(f"Cancelled {cancel_qty} x {item_name}!")
                st.rerun()
            if st.button("Clear Cart"):
                for idx, row in order_df.iterrows():
                    st.session_state.stock_rec[row['Item']] += row['Qty']
                update_stock_rec(connection, st.session_state.stock_rec)
                st.session_state.order_menu = {}
                st.session_state.tax_lis = {}
                st.success("Cart cleared!")
                st.rerun()
        else:
            st.info("Cart is empty.")

    with tab_bill:
        st.subheader("💰 Generate Bill")
        if st.session_state.order_menu:
            order_df = pd.DataFrame.from_dict(st.session_state.order_menu, orient='index', columns=['Item', 'Qty', 'Unit Price'])
            order_df = order_df.groupby(['Item', 'Unit Price'])['Qty'].sum().reset_index()
            order_df['Total'] = order_df['Qty'].astype(int) * order_df['Unit Price']
            #order_df['Total'] = grouped_df['Qty'].astype(int) * order_df['Unit Price']
            subtotal = order_df['Total'].sum()
            tax_lis = st.session_state.tax_lis
            tax_amt = 0.0
            tax_set = set()
            for _, row in order_df.iterrows():
                item = row['Item']
                tax_cat = tax_lis.get(item, 'Standard')
                tax_rate = st.session_state.tax_data.get(tax_cat, 0.0)
                tax_set.add(tax_rate)
                tax_amt += float(row['Total']) * float(tax_rate)
            gst = max(tax_set) if tax_set else 0.0
            gst *= 100
            cgst = tax_amt / 2
            sgst = cgst
            total_bill = float(subtotal) + float(tax_amt)

            st.write("**💳 Bill Statement**")
            ist = pytz.timezone("Asia/Kolkata")
            current_time = datetime.now(ist).strftime("%Y-%m-%d %I:%M %p")
            st.write(f"**Date:** {current_time}")
            st.dataframe(order_df[['Item', 'Qty', 'Total']])
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Subtotal", f"Rs.{subtotal:.2f}")
            col2.metric("Tax Amount", f"Rs.{tax_amt:.2f}")
            col3.metric("CGST", f"Rs.{cgst:.2f}")
            col4.metric("SGST", f"Rs.{sgst:.2f}")
            col5.metric("Max Apllied GST", f"{gst}%")
            st.metric("**Total Bill**", f"Rs.{total_bill:.2f}")
            order_df['Total'] = pd.to_numeric(order_df['Total'], errors='coerce')
            order_df = order_df.dropna(subset=['Total']).query('Total > 0')
            if len(order_df) > 0:
                fig_pie, ax = plt.subplots(figsize=(8, 6))
                order_df.plot(kind='pie', y='Total', labels=order_df['Item'], ax=ax, autopct='%1.1f%%', startangle=90)
                ax.set_title('Bill Breakdown')
                ax.set_ylabel('')  # Remove y-label for cleaner pie
                st.pyplot(fig_pie)
            #fig_pie, ax = plt.subplots()
            #order_df.plot(kind='pie', y='Total', labels=order_df['Item'], ax=ax, autopct='%1.1f%%')
            #ax.set_title('Bill Breakdown')
            #st.pyplot(fig_pie)
            if st.button("Confirm & Insert Sales to DB"):
                tmp_lis = order_df[['Item', 'Qty', 'Total']].values.tolist()
                insert_db_data(connection, tmp_lis)
                st.session_state.order_menu = {}
                st.session_state.tax_lis = {}
                st.success("Sales inserted! Cart cleared.")
                st.rerun()
        else:
            st.warning("No items in cart.")

# --- Corporate Portal ---
elif portal == "Corporate (Admin)":
    st.error("""
    🔒 **DEMO MODE WARNING**  
    This is a **public demo** of the Admin Dashboard.  
    - **Do NOT edit data** (e.g., stock, prices, taxes)—changes are simulated or may affect shared demo data.  
    - View features only: Explore reports, graphs, and maintenance tools.  
    - For real use, contact the developer or use a private instance.  
    Questions? Check the GitHub repo: [unixanand/restaurant-app](https://github.com/unixanand/restaurant-app).
    """)
    user_file = "./Files/user_list.txt"
    if os.path.exists(user_file):
        with open(user_file, "r") as f:
            allowed = set(line.strip() for line in f)
    else:
        allowed = set()  # Or default users
    username = st.sidebar.text_input("Enter Username to login Admin Portal", type="password")
    st.sidebar.button("Ok")
    #if username not in allowed:
        #st.warning("Invalid user - Corporate access denied!")
        #st.stop()
    st.sidebar.success(f"Welcome, {username}!")
    st.header("⚙️ Corporate Portal: Admin Dashboard")
    tab_admin1, tab_admin2, tab_admin3,tab_admin4 = st.tabs(["Maintenance", "Graphs & Reports", "Dynamic Reports", "Bulk Orders"])
    with tab_admin1:
        st.subheader("1. View Current Stock")
        if st.button("Refresh & Show Stock"):
            st.session_state.stock_rec = get_stock_data(connection)
            df_stock = pd.DataFrame(list(st.session_state.stock_rec.items()), columns=['Item', 'Available Stock'])
            st.dataframe(df_stock)

        st.subheader("2. Load Shortage Stocks")
        if st.button("Get Shortage Stock"):
            st.session_state.stock_rec = get_shortage_stock_data(connection)
            df_stock = pd.DataFrame(list(st.session_state.stock_rec.items()), columns=['Item', 'Available Stock'])
            st.dataframe(df_stock)
            
        if st.button("Load Stock"):
            load_shortage_stock_data(connection)
        st.subheader("3. Item Addition/Deletion")
        category = st.selectbox("Category", ["Coffee", "Tea", "Chat", "Spl"])
        action = st.selectbox("Action", ["Add", "Delete"])
        with st.form("item_add_del"):
            if action != 'Delete' :
                item_name = st.text_input("Item Name")
            else : 
                if category == 'Coffee':
                    df_items = fetch_coffee_df(connection)
                elif category == 'Tea':
                    df_items = fetch_tea_df(connection)
                elif category == 'Chat':
                    category = 'Both'
                    df_items = fetch_chat_df(connection, category)
                elif category == 'Spl':
                    df_items = fetch_snack_df(connection)
                item_options = df_items.set_index('ItemNo')['Name'].to_dict()
                item_no = st.selectbox("Select Item", options=list(item_options.keys()), format_func=lambda x: f"{x}: {item_options[x]}")
                item_name = item_options[item_no]
            if action != 'Delete' :
                price = st.number_input("Price (for Add)", min_value=0.0, value=0.0)
            if action != 'Delete' :
                if category == "Chat"  :
                    item_category = st.text_input("Enter VEG / NV")
            if action != 'Delete' :
                if category == 'Coffee' or  category == 'Tea':
                    tax_slab = st.text_input("Tax Tier (TIER1)")
                else:
                    tax_slab = st.text_input("Tax Tier (TIER2/3)")
                    
            submitted = st.form_submit_button(f"{action} Item")
            if submitted:
                cursor = connection.cursor()
                if action == "Add":
                    if category == "Coffee":
                        ins_stmt = "INSERT INTO coffee_menu_tbl(coffee_name, price, tax_category) VALUES (%s, %s, %s)"
                        cursor.execute(ins_stmt, (item_name, price, tax_slab))
                    elif category == "Tea":
                        ins_stmt = "INSERT INTO tea_menu_tbl(tea_name, price, tax_category) VALUES (%s, %s, %s)"
                        cursor.execute(ins_stmt, (item_name, price, tax_slab))
                    elif category == "Chat":
                        ins_stmt = "INSERT INTO chat_menu_tbl(chat_name, price, tax_category, category) VALUES (%s, %s, %s, %s)"
                        cursor.execute(ins_stmt, (item_name, price, tax_slab, item_category))
                    else:
                        ins_stmt = "INSERT INTO special_snacks_tbl(item_name, price, tax_category) VALUES (%s, %s, %s)"
                        cursor.execute(ins_stmt, (item_name, price, tax_slab))
                else:  # Delete
                    if category == "Coffee":
                        del_stmt = "UPDATE coffee_menu_tbl SET delete_flag='Y' WHERE coffee_name = %s"
                        cursor.execute(del_stmt, (item_name,))
                    elif category == "Tea":
                        del_stmt = "UPDATE tea_menu_tbl SET delete_flag='Y' WHERE tea_name = %s"
                        cursor.execute(del_stmt, (item_name,))
                    elif category == "Chat":
                        del_stmt = "UPDATE chat_menu_tbl SET delete_flag='Y' WHERE chat_name = %s"
                        cursor.execute(del_stmt, (item_name,))
                    else:
                        del_stmt = "UPDATE special_snacks_tbl SET delete_flag='Y' WHERE item_name = %s"
                        cursor.execute(del_stmt, (item_name,))
                connection.commit()
                cursor.close()
                st.success(f"{action}ed {item_name} in {category}!")
                st.rerun()
        st.subheader("4. Update Item Prices")
        category_price = st.selectbox("Category for Price Update", ["Coffee", "Tea", "Chat", "Spl"], key="price_cat")
        with st.form("price_update"):
            if category_price == "Coffee":
                df_items = fetch_coffee_df(connection)
            elif category_price == "Tea":
                df_items = fetch_tea_df(connection)
            elif category_price == "Chat":
                df_items = fetch_chat_df(connection, "Both")
            else:
                df_items = fetch_snack_df(connection)
                
            if not df_items.empty:
                item_options = df_items.set_index('ItemNo')['Name'].to_dict()
                item_no = st.selectbox("Select Item", options=list(item_options.keys()), format_func=lambda x: f"{x}: {item_options[x]}")
                matching_row = df_items[df_items['ItemNo'] == item_no]
                if 'current_price' not in st.session_state:
                    st.session_state.current_price = 0.0
                st.session_state.current_price = float(matching_row['Price'].values[0])
                if st.form_submit_button("Show Price"):
                    st.metric("Current Price", st.session_state.current_price)                    
                new_price = st.number_input("New Price", min_value=0.0, value=0.0)
                submitted = st.form_submit_button("Update Price")
    
                
    
                if submitted:
                    cursor = connection.cursor()
                    item_name = item_options[item_no]
                    if category_price == "Coffee":
                        upd_stmt = "UPDATE coffee_menu_tbl SET price = %s WHERE coffee_name = %s"
                        cursor.execute(upd_stmt, (new_price, item_name))
                    elif category_price == "Tea":
                        upd_stmt = "UPDATE tea_menu_tbl SET price = %s WHERE tea_name = %s"
                        cursor.execute(upd_stmt, (new_price, item_name))
                    elif category_price == "Chat":
                        upd_stmt = "UPDATE chat_menu_tbl SET price = %s WHERE chat_name = %s"
                        cursor.execute(upd_stmt, (new_price, item_name))
                    else:
                        upd_stmt = "UPDATE special_snacks_tbl SET price = %s WHERE item_name = %s"
                        cursor.execute(upd_stmt, (new_price, item_name))
                    connection.commit()
                    cursor.close()
                    st.success(f"Updated price for {item_name} to Rs.{new_price:.2f}!")
                    st.rerun()
            else:
                st.warning(f"No items in {category_price}.")
                
        st.subheader("5. Show Tax Category")
        if st.button("Get Tax Slabs"):
            st.session_state.tax_rec = load_tax_data(connection)
            df_tax = pd.DataFrame(list(st.session_state.tax_rec.items()), columns=['Tax Slab', 'Tax Amt'])
            st.dataframe(df_tax)
            
        st.subheader("6. Update Tax Amount")
        st.session_state.tax_rec = load_tax_data(connection)
        df_tax = pd.DataFrame(list(st.session_state.tax_rec.items()), columns=['Tax Slab', 'Tax Amt'])
        tax_category = st.selectbox("Select Tax Category", options=df_tax['Tax Slab'].unique())
        tax_amount = st.text_input("Tax Amount", value=0.0)
        if st.button("Update Tax Amount"):
            update_tax_amt(connection,tax_category,tax_amount)
            st.success("Tax Amount updated successfully!")
            st.rerun()
            
    with tab_admin2:
        st.subheader("Sales Graphs")
        period = st.selectbox("Period", ["Daily", "Weekly", "Monthly"])
        category = st.selectbox("Category", ["Coffee", "Tea", "Chat", "Spl", "Overall"])
        if st.button("Generate Chart"):
            if category == "Coffee":
                fig = coffee_sales_fig(connection, period)
                df = coffee_sales_data(connection, period)
                if fig:
                    st.pyplot(fig)
                else:
                    st.info("No coffee sales data.")
                st.subheader(f"{period} Sales Data")
                st.dataframe(df)
            elif category == "Tea":
                fig = tea_sales_fig(connection, period)
                df = tea_sales_data(connection, period)
                if fig:
                    st.pyplot(fig)
                else:
                    st.info("No tea sales data.")
                st.subheader(f"{period} Sales Data")
                st.dataframe(df)
            elif category == "Chat":
                fig = chat_sales_fig(connection, period)
                df = chat_sales_data(connection, period)
                if fig:
                    st.pyplot(fig)
                else:
                    st.info("No chat sales data.")
                st.subheader(f"{period} Sales Data")
                st.dataframe(df)
            elif category == "Spl":
                fig = Spl_sales_fig(connection, period)
                df = Spl_sales_data(connection, period)
                if fig:
                    st.pyplot(fig)
                else:
                    st.info("No snacks sales data.")
                st.subheader(f"{period} Sales Data")
                st.dataframe(df)
            else:
                fig = overall_sales_fig(connection, period)
                df = overall_sales_data(connection, period)
                if fig:
                    st.pyplot(fig)
                else:
                    st.info("No sales data.")
                st.subheader(f"{period} Sales Data")
                st.dataframe(df)

    with tab_admin3:
        st.subheader("Dynamic Reports")
        tabG, tabW, tabM, tabA  = st.tabs(["Generic Report", "WeeklyReport", "Monthly Report", "Report as Your Choice"])
        with tabG:
            st.title("User Option")
            user_choice = st.radio(
                "Do you need to write excel into local dir? (Y/N)", 
                 options=["Y", "N"],
                index=None,  
                key="excel_radio"  
                )
            report_choice = st.radio(
                "Select Item for report generation", 
                 options=["Coffee", "Tea", "Chat", "Snacks", "All"],
                index=None,  
                key="item_radio"  
                )
            date_start = st.date_input("Start Date")
            date_end = st.date_input("End Date")
            query = ""
            if st.button("Generate Dynamic Report"):
                if report_choice == "All" :
                    rep_name="Overall"
                    fp = open("./Files/pg_generic_sql.txt","r")
                    query = fp.read()
                    fp.close()
                elif report_choice == "Coffee" :
                    rep_name="Coffee"
                    fp = open("./Files/pg_generic_coffee_sql.txt","r")
                    query = fp.read()
                    fp.close()
                elif report_choice == "Tea" :
                    rep_name="Tea"
                    fp = open("./Files/pg_generic_tea_sql.txt","r")
                    query = fp.read()
                    fp.close()
                elif report_choice == "Chat" :
                    rep_name="Chat"
                    fp = open("./Files/pg_generic_chat_sql.txt","r")
                    query = fp.read()
                    fp.close()
                elif report_choice == "Snacks" :
                    rep_name="Snacks"
                    fp = open("./Files/pg_generic_snacks_sql.txt","r")
                    query = fp.read()
                    fp.close()
                cursor = connection.cursor()
                params = {
                    'date_start': date_start,  
                    'date_end': date_end       
                }

                cursor.execute(query, params)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                df_sales = pd.DataFrame(rows, columns=columns)
                df_sales.columns = df_sales.columns.str.lower()
                st.dataframe(df_sales)
                if not df_sales.empty :
                
                    fig_pie, ax = plt.subplots()
                    df_sales['sales_amt'] = pd.to_numeric(df_sales['sales_amt'], errors='coerce')
                    sales_by_item = df_sales.groupby('item_name')['sales_amt'].sum()
                    sales_by_item.plot(kind='pie', ax=ax, autopct='%1.1f%%', labels=sales_by_item.index)
                    ax.set_title('Sales Breakdown by Item')
                    st.pyplot(fig_pie)
                
                    if user_choice == 'N' :
                        output = io.BytesIO()
                        with pd.ExcelWriter(output, engine='openpyxl') as writer:
                            df_sales.to_excel(writer, index=False, sheet_name='Sales Data')
                            output.seek(0)

                        st.download_button(
                            "Download Dynamic Excel", 
                            output.getvalue(), 
                            file_name='in_mem_dynamic_report.xlsx',
                            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                        )

                        st.success("Excel ready for download (generated in memory—no file written to disk).")
                    else :
                        file_name = os.path.join(REPORTS_DIR, f"dynamic_{report_choice}_sales_report.xlsx")
                        df_sales.to_excel(file_name, index=False)
                        with open(file_name, 'rb') as f:
                            st.download_button("Download Dynamic Excel", f.read(), file_name=file_name)
                else:
                    st.info("No sales data for selected range.")

        with tabW:
            if st.button("Generate Xcel Report"):
                df_sales =  pull_week_data(connection)
                st.dataframe(df_sales)
                file_name = os.path.join(REPORTS_DIR,"dynamic_Weekly_report.xlsx")
                df_sales.to_excel(file_name, index=False)
                with open(file_name, 'rb') as f:
                     st.download_button("Download Dynamic Excel", f.read(), file_name=file_name)
                
            
            if st.button("Show Visuals"):
                item_lis = Week_sale_items(connection)
                days = sorted(set(item[0] for item in item_lis))  
                item_types = sorted(set(item[1] for item in item_lis))
                num_days = len(days)
                

                sale_amt = np.zeros((len(days), len(item_types)))
                for day_idx, day in enumerate(days):
                    for item_idx, items in enumerate(item_types):
                        for item in item_lis:
                            
                            if item[0] == day and item[1] == items:
                                sale_amt[day_idx, item_idx] = item[2]
                    
                    
                colors = plt.cm.tab10(np.linspace(0, 1, num_days))
                
                if num_days > 0:
                    bar_width = 0.8/num_days
                x = np.arange(len(item_types))
                fig, ax = plt.subplots()

                for day_idx, day in enumerate(days):
                    offset = (day_idx - (num_days - 1) / 2) * bar_width
                    ax.bar(x + offset, sale_amt[day_idx], bar_width, label=day, color=colors[day_idx])

                ax.set_xlabel('Item Type')
                ax.set_ylabel('Sales Amount')
                ax.set_title('Item Sales by Day')
                ax.set_xticks(x)
                ax.set_xticklabels(item_types, rotation=45, ha='right')
                ax.legend()
                ax.grid(True, axis='y', linestyle='--', alpha=0.7)

                st.pyplot(fig)

        with tabM:
            item_lis = []
            if st.button("Generate Monthly Xcel Report"):
                df_sales =  pull_month_data(connection)
                st.dataframe(df_sales)
                file_name = f"./reports/dynamic_Monthly_report.xlsx"
                df_sales.to_excel(file_name, index=False)
                with open(file_name, 'rb') as f:
                     st.download_button("Download Dynamic Excel", f.read(), file_name=file_name)

            option = st.selectbox(
                label="Choose an option",
                options=["Item & Qty", "Item & Sales"]
                )

            if st.button("Show Visual"):
                
                if option == "Item & Qty" :
                    
                    item_lis = get_month_data(connection)
                    weeks = sorted(set(item[0] for item in item_lis))  
                    item_types = sorted(set(item[1] for item in item_lis))
                    num_weeks = len(weeks)
                

                    sale_amt = np.zeros((len(weeks), len(item_types)))
                    for week_idx, week in enumerate(weeks):
                        for item_idx, items in enumerate(item_types):
                            for item in item_lis:
                            
                                if item[0] == week and item[1] == items:
                                    sale_amt[week_idx, item_idx] = item[3]
                    
                    
                    colors = plt.cm.tab10(np.linspace(0, 1, num_weeks))
                

                    bar_width = 0.8/num_weeks
                    x = np.arange(len(item_types))
                    fig, ax = plt.subplots()

                    for week_idx, week in enumerate(weeks):
                        offset = (week_idx - (num_weeks - 1) / 2) * bar_width
                        ax.bar(x + offset, sale_amt[week_idx], bar_width, label=week, color=colors[week_idx])

                    ax.set_xlabel('Item Type')
                    ax.set_ylabel('Sales Amount')
                    ax.set_title('Item Sales by Week')
                    ax.set_xticks(x)
                    ax.set_xticklabels(item_types, rotation=45, ha='right')
                    ax.legend()
                    ax.grid(True, axis='y', linestyle='--', alpha=0.7)

                    st.pyplot(fig)

                else :
                    
                    item_lis = get_month_data(connection)
                    weeks = sorted(set(item[0] for item in item_lis))  
                    item_types = sorted(set(item[1] for item in item_lis))
                    num_weeks = len(weeks)
                

                    sale_amt = np.zeros((len(weeks), len(item_types)))
                    for week_idx, week in enumerate(weeks):
                        for item_idx, items in enumerate(item_types):
                            for item in item_lis:
                            
                                if item[0] == week and item[1] == items:
                                    sale_amt[week_idx, item_idx] = item[4]
                    
                    
                    colors = plt.cm.tab10(np.linspace(0, 1, num_weeks))
                

                    bar_width = 0.8/num_weeks
                    x = np.arange(len(item_types))
                    fig, ax = plt.subplots()

                    for week_idx, week in enumerate(weeks):
                        offset = (week_idx - (num_weeks - 1) / 2) * bar_width
                        ax.bar(x + offset, sale_amt[week_idx], bar_width, label=week, color=colors[week_idx])

                    ax.set_xlabel('Item Type')
                    ax.set_ylabel('Sales Quantity')
                    ax.set_title('Item Sales by Week')
                    ax.set_xticks(x)
                    ax.set_xticklabels(item_types, rotation=45, ha='right')
                    ax.legend()
                    ax.grid(True, axis='y', linestyle='--', alpha=0.7)

                    st.pyplot(fig)

        with tabA:
            st.header("Welcome to Dynamic Report Generation")
            
            column_map = {
                1 : "value_date",
                2 : "Item_name",
                3 : "Quantity",
                4 : "Sales_Amt"
            }
            field_name_map = {
                1 : "Sales Date",
                2 : "Item Name",
                3 : "Quantity",
                4 : "Sales_Amt"
            }
            item_map = {
                1 : "Coffee",
                2 : "Tea",
                3 : "Chat",
                4 : "Snacks"
            }

            table_map = {
                "Coffee" : "Coffee_menu_tbl",
                "Tea" : "Tea_menu_tbl",
                "Chat" : "Chat_menu_tbl",
                "Snacks" : "special_snacks_tbl"
            }

            field_map = {
                "Coffee_menu_tbl" : "Coffee_name",
                "Tea_menu_tbl" : "Tea_name",
                "Chat_menu_tbl" : "Chat_name",
                "special_snacks_tbl" : "item_name"
            }

            period_map = {
                1 : "Daily",
                2 : "Weekly",
                3 : "Monthly",
                4 : "Customized Date"
            }

            item_option = st.selectbox(
                label="Choose the Item",
                options=["Coffee", "Tea", "Chat", "Snacks"]
                )

            date_start = st.date_input("From Date")
            date_end = st.date_input("To Date")
            if "reset_widgets" not in st.session_state:
                st.session_state.reset_widgets = False

            def reset_state():
                st.session_state.reset_widgets = True

            selected_fields = {}
            aggregate_fields = {}
            order_fields = {}
            agg_fields = []
            query_fields = []
            allowed_fields = set()
            allowed_fields = ("Quantity", "Sales_Amt")
            agg_options = []
            order_option = []
            order_flds = []
            ord_fields = {}
            column_names = []
            sql_qry = ""

            st.write("Choose the data Fields")
            
            field_options = ["Value_Date", "Item_Name", "Quantity", "Sales_Amt"]

            for column in field_options:
                if f"selected_{column}" not in st.session_state:
                    st.session_state[f"selected_{column}"] = False
                if f"aggregate_{column}" not in st.session_state:
                    st.session_state[f"aggregate_{column}"] = False
                if f"order_{column}" not in st.session_state:
                    st.session_state[f"order_{column}"] = False

            if st.button("Clear Data"):
                reset_state()
                for column in field_options:
                    st.session_state[f"selected_{column}"] = False
                    
            for column in field_options:
                selected_fields[column] = st.checkbox(column,key=f"selected_{column}")

            for column in field_options:
                if selected_fields[column]:
                    query_fields.append(column)
                    if column in allowed_fields :
                        agg_options.append(column)

            st.write("Choose Aggregate Fields")

            for field in agg_options:
                aggregate_fields[field] = st.checkbox(field, key=f"aggregate_{field}")

            for field in agg_options:
                if aggregate_fields[field] :
                    agg_fields.append(field)

            qry_str = "SELECT "      
            for i in range(len(query_fields)) :
                if i == 0 :
                    qry_str += f"{query_fields[i]}"
                else :
                    if query_fields[i] in agg_fields :
                        qry_str += f",SUM({query_fields[i]})"
                    else :
                        qry_str += f",{query_fields[i]}"

            for i in range(len(query_fields)) :
                if query_fields[i] in agg_fields :
                    column_names.append(f"Tot.{query_fields[i]}")
                else :
                    column_names.append(query_fields[i])
                
            qry_str += " FROM sales_dtl_tbl "
            if item_option == "Coffee" :
                qry_str += " WHERE item_name IN (SELECT coffee_name FROM coffee_menu_tbl )"
            elif item_option == "Tea" :
                qry_str += " WHERE item_name IN (SELECT tea_name FROM tea_menu_tbl )"
            elif item_option == "Chat" :
                qry_str += " WHERE item_name IN (SELECT chat_name FROM chat_menu_tbl )"
            else :
                qry_str += " WHERE item_name IN (SELECT item_name FROM special_snacks_tbl )"

            qry_str += f" AND value_date BETWEEN TO_DATE('{date_start}','YYYY-MM-DD') AND TO_DATE('{date_end}','YYYY-MM-DD')"
            if len(agg_fields) != 0 :
                qry_str += " GROUP BY "
                for i in range(len(query_fields)) :
                    if query_fields[i] not in agg_fields :
                        if i == 0 :
                            qry_str += f"{query_fields[i]}"
                        else :
                            qry_str += f",{query_fields[i]}"

            st.write("Choose Order by")
            for i in range(len(query_fields)) :
                if query_fields[i] not in agg_fields :
                    order_option.append(query_fields[i])

            for field in order_option:
                order_fields[field] = st.checkbox(field, key=f"order_{field}")

            for field in order_option:
                if order_fields[field] :
                    order_flds.append(field)
            
            
            if len(order_flds) > 0:
                qry_str += f" ORDER BY "
                for i in range(len(order_flds)) :
                    if i == 0 :
                        qry_str += f"{order_flds[i]}"
                    else :
                        qry_str += f",{order_flds[i]}"
                    
            st.write("Choose asc/desc")
            if st.session_state.reset_widgets:
                st.session_state.order_radio = None
                
            order_choice = st.radio(
                "Choose data order", 
                 options=["asc", "desc"],
                index=None,  
                key="order_radio"  
                )

            if order_choice == "desc" :
                qry_str += f" DESC"
            if order_choice == "asc" :
                qry_str += f" ASC"
            sql_qry = qry_str
            if(st.button('show Query')):
                with st.expander("View Generated SQL Query", expanded=True):
                    st.code(sql_qry, language="sql")  
            
            if st.button(f"Generate {item_option} Sales Xcel Report"):
                sales_rec = execute_qry(connection, qry_str,column_names)
                st.dataframe(sales_rec)
                
                file_name = f"./reports/dynamic_{item_option}_sales_report.xlsx"
                sales_rec.to_excel(file_name, index=False)
                with open(file_name, 'rb') as f:
                     st.download_button("Download Dynamic Excel", f.read(), file_name=file_name)

        with tab_admin4:
            st.subheader("Process Bulk Orders")
            order_list = {}
            tot_price = 0.0
            tot_tax = 0.0
            if 'bulk_lis' not in st.session_state:
                st.session_state.bulk_lis = []
            tmp_lis = []
            log_str = ""
            
            log_path = os.path.join(BULK_DIR, "bulk_order.log")
            file_path = os.path.join(BULK_DIR, "loaded_file.txt")

            logging.basicConfig(level=logging.INFO, filename=os.path.join(BULK_DIR, 'bulk_order.log'), force=True)
            logger = logging.getLogger()
            logger.info("App started")
            
            with open(log_path,"a") as fp :
            
                current_date = date.today()
            
                print(f"Log date: {current_date}\n",file=fp)
                log_str += f"Log date: {current_date}\n"
            
                st.title("Bulk menu Reader")
                fname = open(file_path,"w")

                uploaded_file = st.file_uploader("Upload the Order File", type=["xlsx", "xls"])
                print(f"Order file: {uploaded_file}\n",file=fp)
                log_str += f"Order file: {uploaded_file}\n"
                if uploaded_file is not None :
                    target_path = os.path.join(BULK_DIR, "bulk_order.xlsx")
                    with open(target_path, "wb") as f:
                        f.write(uploaded_file.getvalue())
                        f.close()
                    fname.write(log_str)
                fname.close()
                fname = open(file_path,"r")
                file=""
                pattern = r"name='([^']+)[.]"
                for line in fname.readlines():
                    match =  re.search(pattern,line)
                    if match :
                        file = match.group(1)
                dup_file = check_bulk_header(connection,file)
                
                if dup_file == 1 :
                    st.error("Duplicate file loaded!")
                status = "OPEN"
                load_bulk_header(connection,file, status)
                message = "Loaded"
                insert_log(connection,file,message)
                fname.close()

                if uploaded_file is not None and dup_file == 0 :
                    try:
                        df = pd.read_excel(uploaded_file)
        
                        required_columns = ["Item name", "Quantity"]
                        if all(col in df.columns for col in required_columns):
                            df = df[required_columns]
            
                            st.write("### Bulk Orders from File")
                            st.dataframe(df)
                            for index, row in df.iterrows():
                                item_name = row["Item name"]
                                valid = validate_item(connection,item_name)
                            
                                if valid == 0 :
                                    print(f"Invalid item- {item_name}\n",file=fp)
                                    log_str += f"Invalid item- {item_name}\n"
                                    message = f"Invalid item- {item_name}\n"
                                    insert_log(connection,file,message)
                                    continue
                            
                                quantity = row["Quantity"]
                            
                                if quantity < 0 or quantity > 100 :
                                    print(f"Invalid quantity - {item_name}\n", file=fp)
                                    log_str += f"Invalid quantity - {item_name}\n"
                                    message = f"Invalid quantity - {item_name}\n"
                                    insert_log(connection,file,message)
                                else :
                                    order_list[item_name] = quantity
                                    print(f"Feteching Item - {item_name}\n", file=fp)
                                    log_str += f"Feteching Item - {item_name}\n"
                                    message = f"Feteching Item - {item_name}\n"
                                    insert_log(connection,file,message)
                                    fp.flush()
                        
                            st.write("### Summary")
                            st.write(f"Total loaded Items: {len(df)}")
                            st.write(f"Total loaded Quantity: {df['Quantity'].sum()}")
                        
                        else:
                            st.error("The Excel file must contain 'Item name' and 'Quantity' columns.")
            
                    except Exception as e:
                        st.error(f"Error reading the Excel file: {e}")
                else:
                    st.info("Upload the bulk order file to process!")

                if st.button("Process Order") :
                
                    st.write("Processed Orders ###")
                    st.session_state.bulk_lis = []
                    for item, qty in order_list.items() :
                        avail_stock, qty = get_item_stock(connection, item, qty)
                        if qty != 0 :
                            price, tax = get_item_price(connection,item,qty)
                            print(f"processing {item} and quantity : {qty}\n",file=fp)
                            log_str += f"processing {item} and quantity : {qty}\n"
                            message = f"processing {item} and quantity : {qty}\n"
                            insert_log(connection,file,message)
                            tot_price += float(price)
                            tot_tax += float(tax)
                            tmp_lis = [item,qty,price,tax]
                            st.session_state.bulk_lis.append(tmp_lis)
                        else :
                            print(f"{item} {qty} - Rejected due to stock shortage\n",file=fp)
                            log_str += f"{item} {qty} - Rejected due to stock shortage\n"
                            message = f"{item} {qty} - Rejected due to stock shortage\n"
                            insert_log(connection,file,message)
                            fp.flush()
                    df = pd.DataFrame(st.session_state.bulk_lis, columns = ["Item Name","Quantity","Price","Tax"])
                    st.dataframe(df)
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Subtotal", f"Rs.{tot_price:.2f}")
                    col2.metric("Tax Amt", f"Rs.{tot_tax:.2f}")
                    col3.metric("Tot. Bill Amt", f"Rs.{tot_price+tot_tax:.2f}")
                    print(f"Tot. Bill Amt For current order", f"Rs.{tot_price+tot_tax:.2f}",file=fp)
                    log_str += f"Tot. Bill Amt For current order, Rs.{tot_price+tot_tax:.2f}"
                    message = f"Tot. Bill Amt For current order =  Rs.{tot_price+tot_tax:.2f}"
                    insert_log(connection,file,message)
                    fp.close()
                    status = "Processed"
                    update_bulk_header(connection,file,status)
                    insert_db_data(connection, st.session_state.bulk_lis)
                    fig_pie, ax = plt.subplots()
                    
                    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce')
                    sales_by_item = df.groupby('Item Name')['Quantity'].sum()
                    
                    
                    if len(sales_by_item) > 0 :
                        sales_by_item.plot(kind='pie', ax=ax, autopct='%1.1f%%', labels=sales_by_item.index)
                        ax.set_title('Sales Breakdown by Item')
                        st.pyplot(fig_pie)

                if st.button(f"Generate Bill in Xcel Report"):
                    bill_rec = pd.DataFrame(st.session_state.bulk_lis, columns = ["Item Name","Quantity","Price","Tax"])
                                
                    file_name = f"./reports/Bill_Statement.xlsx"
                    bill_rec.to_excel(file_name, index=False)
                    with open(file_name, 'rb') as f:
                         st.download_button("Download Bill Statement", f.read(), file_name=file_name)


# Footer
st.sidebar.markdown("---")
st.sidebar.info("Dashboard powered by Streamlit + PostgreSQL")
