cmake_minimum_required(VERSION 3.5.0 FATAL_ERROR)

project(my_app VERSION 0.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 14)

if (CMAKE_COMPILER_IS_GNUCC)
    target_compile_options(my_app PRIVATE "-Wall -Wextra -pedantic")
endif()

# Make sure to use host Qt programs, because when cross-compiling Qt for some reason uses target
if (UNIX)
    foreach(PROGRAM moc rcc uic)
        find_program(QT_NATIVE_EXECUTABLE_${PROGRAM} ${PROGRAM} NO_CMAKE_FIND_ROOT_PATH)
        if (QT_NATIVE_EXECUTABLE_${PROGRAM})
            message(STATUS "Found ${PROGRAM} ${QT_NATIVE_EXECUTABLE_${PROGRAM}}")
            add_executable(Qt5::${PROGRAM} IMPORTED)
            set_property(TARGET Qt5::${PROGRAM} PROPERTY IMPORTED_LOCATION ${QT_NATIVE_EXECUTABLE_${PROGRAM}})
        endif()
    endforeach()
endif()

set(CMAKE_INCLUDE_CURRENT_DIR ON)
set(CMAKE_AUTOMOC ON)

# Find the QtWidgets library
find_package(Qt5 5.5.0 REQUIRED COMPONENTS Core Gui Network Widgets)
# TODO: add Info.plist https://cmake.org/cmake/help/v3.5/prop_tgt/MACOSX_BUNDLE.html MACOSX_BUNDLE
add_executable(my_app WIN32
    src/main.cpp
    src/main_window.h
    src/main_window.cpp
)
target_link_libraries(my_app Qt5::Core Qt5::Gui Qt5::Network Qt5::Widgets)
target_compile_definitions(my_app PRIVATE "QT_DISABLE_DEPRECATED_BEFORE=0x050500")

install(TARGETS my_app RUNTIME DESTINATION bin)
if (UNIX)
    install(FILES "my_app.desktop" DESTINATION "share/applications")
    set(ICON_SIZES 14 16 22 24 32 36 48 64 72 96 128 192 256 512)
    foreach(size ${ICON_SIZES})
        install(FILES "icons/${size}x${size}.png" DESTINATION "share/icons/hicolor/${size}x${size}/apps" RENAME "my_app.png")
    endforeach()
endif()
