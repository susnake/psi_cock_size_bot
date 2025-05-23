# psi_cock_size_bot

![image](https://github.com/user-attachments/assets/e2310811-3ddf-4581-8bff-996d5f892b3e)


git clone https://github.com/susnake/psi_cock_size_bot.git

cd psi_cock_size_bot

vim .env            # впишите psi_chat_bot=<telegram_token>  и GEMINI_API_KEY=<GEMINI_API_KEY>

docker run -d --name psi-bot --restart unless-stopped --env-file .env susnake/psi_cock_size_bot:latest
