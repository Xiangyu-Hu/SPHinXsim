set(CMAKE_MODULE_PATH ${CMAKE_MODULE_PATH} ${CMAKE_SOURCE_DIR}/cmake) # main (top) cmake dir

set(CMAKE_VERBOSE_MAKEFILE on)

STRING(REGEX REPLACE ".*/(.*)" "\\1" CURRENT_FOLDER ${CMAKE_CURRENT_SOURCE_DIR})
PROJECT("${CURRENT_FOLDER}")

SET(LIBRARY_OUTPUT_PATH ${PROJECT_BINARY_DIR}/lib)
SET(EXECUTABLE_OUTPUT_PATH "${PROJECT_BINARY_DIR}/bin/")
SET(BUILD_INPUT_PATH "${EXECUTABLE_OUTPUT_PATH}/input")
SET(BUILD_RELOAD_PATH "${EXECUTABLE_OUTPUT_PATH}/reload")

# Remove stale inputs from previous configurations before copying current test data.
file(REMOVE_RECURSE ${BUILD_INPUT_PATH})
file(MAKE_DIRECTORY ${BUILD_INPUT_PATH})
file(COPY ${CMAKE_CURRENT_SOURCE_DIR}/data/ DESTINATION ${BUILD_INPUT_PATH})

# Regression test files, one folder per case named after its json stem. SPHinXsys reads the
# database files from the input folder, i.e. the folder of the json, so they are copied there,
# and restart/ and reload/ go to the output root of the case, ./<json stem>.
file(GLOB REGRESSION_CASE_DIRS LIST_DIRECTORIES true "${CMAKE_CURRENT_SOURCE_DIR}/regression_test_tool/*")
foreach(CASE_DIR ${REGRESSION_CASE_DIRS})
    if(NOT IS_DIRECTORY ${CASE_DIR})
        continue()
    endif()
    get_filename_component(CASE_NAME ${CASE_DIR} NAME)
    file(GLOB DATABASE_FILES LIST_DIRECTORIES false "${CASE_DIR}/*")
    file(COPY ${DATABASE_FILES} DESTINATION ${BUILD_INPUT_PATH})
    foreach(CHECKPOINT_DIR restart reload)
        if(IS_DIRECTORY "${CASE_DIR}/${CHECKPOINT_DIR}")
            file(COPY "${CASE_DIR}/${CHECKPOINT_DIR}" DESTINATION "${EXECUTABLE_OUTPUT_PATH}/${CASE_NAME}")
        endif()
    endforeach()
endforeach()

if(SPHINXSIM_BUILD_PROJECT)
    if(EXISTS "${CMAKE_SOURCE_DIR}/project/simulation/3d_cases")  
        file(COPY ${CMAKE_SOURCE_DIR}/project/simulation/3d_cases/ DESTINATION ${BUILD_INPUT_PATH})
    endif()
endif()

add_executable(${PROJECT_NAME})
aux_source_directory(. DIR_SRCS)
target_sources(${PROJECT_NAME} PRIVATE ${DIR_SRCS})
target_link_libraries(${PROJECT_NAME} PRIVATE sph_simulation_3d GTest::gtest)
set_target_properties(${PROJECT_NAME} PROPERTIES VS_DEBUGGER_WORKING_DIRECTORY "${EXECUTABLE_OUTPUT_PATH}")

include(GoogleTest)
gtest_discover_tests(${PROJECT_NAME}
    WORKING_DIRECTORY ${EXECUTABLE_OUTPUT_PATH}
    DISCOVERY_MODE PRE_TEST
    TEST_PREFIX "${PROJECT_NAME}."
    PROPERTIES LABELS "simulation_class")
