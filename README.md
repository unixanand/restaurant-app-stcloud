# 🍽️ St. Cloud Restaurant Dash

**Interactive Streamlit app for seamless restaurant ops: Order, stock, sales & reports!**  
Live demo: [Try it now](https://restaurant-app-stcloud.streamlit.app) | Code: [GitHub](https://github.com/unixanand/restaurant-app-stcloud)

## 🚀 Quick Features
- **Public Portal**: Browse menus (coffee ☕, tea 🫖, chat 🍗🥕, specials 🥂), cart & bill with GST pie charts.
- **Admin Dash**: Stock tweaks, dynamic charts (bar/pie), Excel exports, bulk uploads.
- **Smart Alerts**: Email/SMS for low stock; Postgres backend with daily txn.

## 🛠️ Setup (Local)
```bash
git clone https://github.com/unixanand/restaurant-app-stcloud.git
cd restaurant-app-stcloud
pip install -r requirements.txt
# Set .env (DB/Email secrets)
streamlit run restaurantapp_st_cloud.py
```

## ☁️ Deploy to Streamlit Cloud
- Push to GitHub (public).
- [share.streamlit.io](https://share.streamlit.io) > New app > Select repo > Deploy.
- Add secrets (DB creds) in settings.

**Built with ❤️ in Python + Streamlit + Postgres. Fork & feast!**  
*MIT License. Questions? Open an issue.*