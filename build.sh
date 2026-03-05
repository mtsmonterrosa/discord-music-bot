#!/bin/bash
apt-get update -y
apt-get install -y ffmpeg
pip install -r requirements.txt
```

---

## Instalar dependencias localmente

Abre la terminal dentro de la carpeta `discord-music-bot` y ejecuta:
```
pip install -r requirements.txt
```

---

## Probar el bot localmente
```
python main.py
```

Deberías ver:
```
✅ Bot conectado como: NombreDeTuBot#1234
📡 Servidores: 1
🔄 Comandos sincronizados: 5
```

Para detenerlo: `Ctrl+C`

---

## Subir a GitHub y Render

Ejecuta estos comandos en orden, uno por uno:
```
git init
echo ".env" > .gitignore
git add .
git commit -m "Bot de música Discord"
```

Luego en GitHub.com crea el repositorio `discord-music-bot` (público, vacío) y ejecuta (cambia `TU_USUARIO`):
```
git remote add origin https://github.com/TU_USUARIO/discord-music-bot.git
git branch -M main
git push -u origin main