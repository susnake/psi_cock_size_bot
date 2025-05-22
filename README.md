# psi_cock_size_bot
![image](https://github.com/user-attachments/assets/487a5df1-a084-4b0d-8589-988d83cc6e0a)

git clone https://github.com/susnake/psi_cock_size_bot.git

cd psi_cock_size_bot
cp .env.example .env            # впишите TELEGRAM_BOT_TOKEN

docker run -d --name psi-bot --restart unless-stopped --env-file .env susnake/psi_cock_size_bot:latest
