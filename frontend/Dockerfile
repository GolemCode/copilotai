# Utilisation d'une image Node.js optimisée
FROM node:20-alpine

# Définition du répertoire de travail
WORKDIR /app

# Copie des fichiers de gestion de paquets
COPY package.json package-lock.json* ./

# Installation des dépendances JavaScript
RUN npm install

# Copie du code source de l'interface
COPY . .

# Le port par défaut de Vite
EXPOSE 5173

# Lancement du serveur de développement avec exposition réseau
CMD ["npm", "run", "dev", "--", "--host"]