# psi_cock_size_bot
![image](https://github.com/user-attachments/assets/abecc6d9-ec3e-46c4-ae6b-f2f09ef209fb)

git clone https://github.com/susnake/psi_cock_size_bot.git

cd psi_cock_size_bot

vim .env            # впишите psi_chat_bot=<telegram_token> 

docker run -d --name psi-bot --restart unless-stopped --env-file .env susnake/psi_cock_size_bot:latest
