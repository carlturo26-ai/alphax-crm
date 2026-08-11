import streamlit as st
import extra_streamlit_components as stx

cookie_manager = stx.CookieManager()
st.write(cookie_manager.get_all())

if st.button("Set Cookie"):
    cookie_manager.set("test_cookie", "hello", max_age=86400)
    
if st.button("Get Cookie"):
    st.write(cookie_manager.get("test_cookie"))
