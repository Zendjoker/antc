# Connect Google

1. Go to console.cloud.google.com → create a project named **Jarvis**.
2. APIs & Services → Library → enable **Gmail API** and **Google Calendar API**.
3. Google Auth Platform → Get started → app name **Jarvis**, your email, audience **External** → Create.
4. Audience → Test users → add your Gmail address.
5. Clients → Create client → type **Desktop app** → Create.
6. Copy the Client ID and Client secret into `.env`:
   ```
   GOOGLE_CLIENT_ID=paste-id-here
   GOOGLE_CLIENT_SECRET=paste-secret-here
   ```
7. Run: `python main.py --connect google` → sign in → if you see "Google hasn't verified this app", click **Advanced → Go to Jarvis** → Allow.
8. Every 7 days, when Jarvis says to reconnect: run step 7 again.
