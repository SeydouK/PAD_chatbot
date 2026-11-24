CREATE DATABASE pad_test CHARACTER SET utf8mb4; 
CREATE USER 'pad'@localhost IDENTIFIED BY 'pad_test_2026';
GRANT ALL ON pad_test.* TO 'pad'@'localhost';
CREATE TABLE actualites (id INT AUTO_INCREMENT PRIMARY KEY, titre VARCHAR(200), corps TEXT, statut VARCHAR(20))
INSERT INTO actualites (titre, corps, statut) VALUES
('Nouveau terminal', 'Le port de Dakar a inauguré un nouveau terminal à conteneurs. Les autorités ont salué cet investissement majeur pour le trafic régional et la compétitivité du port.', 'publie'),
('Brouillon', 'Texte provisoire qui ne doit pas être exporté vers le chatbot car il est encore en cours de rédaction par la communication du port.', 'brouillon'),
('Saison des croisières', 'La saison des croisières a été ouverte à Dakar avec l''accueil d''un premier paquebot. Des centaines de touristes ont visité la ville et ses environs.', 'publie');
