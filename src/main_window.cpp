/*
    Copyright (C) <YEAR> by <NAME> Project Contributors

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "main_window.h"

#include <QApplication>
#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>
#include <QWidget>

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
{
    QWidget* container = new QWidget(this);
    QVBoxLayout* layout = new QVBoxLayout(container);

    QLabel* buildInfoLabel = new QLabel("Built on " __DATE__ " " __TIME__, container);
    QPushButton* aboutQtbutton = new QPushButton("About Qt", container);
    connect(aboutQtbutton, &QPushButton::clicked, qApp, &QApplication::aboutQt);

    layout->addWidget(buildInfoLabel);
    layout->addWidget(aboutQtbutton);

    setCentralWidget(container);
}

MainWindow::~MainWindow()
{
}
