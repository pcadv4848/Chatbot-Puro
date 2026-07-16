#!/usr/bin/env bash
cd "$HOME/OpenWA"

# Se o banco ainda não existe (primeira execução), API_MASTER_KEY define a chave
if [ ! -f "$HOME/OpenWA/data/openwa-main.sqlite" ]; then
  export API_MASTER_KEY=PCADV48484848
fi

exec npm start
