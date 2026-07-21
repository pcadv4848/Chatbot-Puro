#!/usr/bin/env bash
cd "$HOME/chatbot/OpenWA"

# Se o banco ainda não existe (primeira execução), API_MASTER_KEY define a chave
if [ ! -f "$HOME/chatbot/OpenWA/data/openwa-main.sqlite" ]; then
  export API_MASTER_KEY=PCADV48484848
fi

exec npm start
